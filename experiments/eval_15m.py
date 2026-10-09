"""Phase 3: evaluate three 15m strategies with the same honest walk-forward as Phase 2.

A. Trend pullback: daily + 4h trend filter, 15m RSI re-cross entry, 1.5/3 ATR bracket.
B. Volatility breakout: 20-bar 15m breakout when C's volatility score is in the
   top 20% of its training scores, 1/2 ATR bracket.
C. Gradient boosting: P(+2 ATR before -1 ATR within 16 bars) per side, trade the
   better side when expected R after costs >= +0.1R, 1/2 ATR bracket.

Every strategy is replayed through the evaluator under taker costs (decides
pass/fail) and a maker-entry scenario, against random entries with the same
bracket at a matched trade count.

Run:  python experiments/eval_15m.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.harness import (  # noqa: E402
    PROBE_SEED,
    REPORT_DIR,
    SYMBOLS,
    evaluate_trades,
    load_frames,
    matched_rate,
    pass_checks,
    positive_windows,
    random_sides,
    report_header,
    report_line,
    round_trip_cost_r,
    summarize_trades,
    train_mask,
    walk_forward_schedule,
)
from experiments.train_gbm import label_metrics  # noqa: E402
from src.barrier import barrier_labels  # noqa: E402
from src.features import build_features, feature_columns  # noqa: E402
from src.gbm_signal import SignalModel  # noqa: E402
from src.strategy_evaluator import CostModel  # noqa: E402
from src.trade_plan import TradePlan  # noqa: E402

BASE = "15m"
BAR = 900
CONTEXT = ("1h", "4h", "1d")
COLUMNS = feature_columns(CONTEXT)
HORIZON = 16  # bars = 4 hours
HOLD_SECONDS = HORIZON * BAR
LABEL_SECONDS = (HORIZON + 1) * BAR
WARMUP_DAYS = 30  # daily EMA21 needs ~3 weeks of closed daily candles
BRACKETS = {"A_pullback": (1.5, 3.0), "B_breakout": (1.0, 2.0), "C_gbm": (1.0, 2.0)}
COSTS = {
    "taker": CostModel(fee_bps=4.0, slippage_bps=2.0),
    "maker_entry": CostModel(fee_bps=4.0, slippage_bps=2.0, entry_fee_bps=2.0, entry_slippage_bps=0.0),
}


# ---------------------------------------------------------------------------
# Strategy rules (pure, fixed parameters)
# ---------------------------------------------------------------------------

def pullback_sides(feats: pd.DataFrame, low: float = 40.0, high: float = 60.0) -> np.ndarray:
    """+1 / -1 where the 15m RSI re-crosses in the direction of the daily and 4h trend."""
    rsi = feats["rsi"].to_numpy(float)
    prev = np.r_[np.nan, rsi[:-1]]
    up = (feats["d1_ema_dist_21"].to_numpy(float) > 0) & (feats["h4_ema_dist_50"].to_numpy(float) > 0)
    down = (feats["d1_ema_dist_21"].to_numpy(float) < 0) & (feats["h4_ema_dist_50"].to_numpy(float) < 0)
    long = up & (prev < low) & (rsi >= low)
    short = down & (prev > high) & (rsi <= high)
    return long.astype(int) - short.astype(int)


def breakout_sides(high: np.ndarray, low: np.ndarray, close: np.ndarray, lookback: int = 20) -> np.ndarray:
    """+1 when the close breaks above the previous ``lookback`` highs, -1 below their lows."""
    prior_high = pd.Series(high).shift(1).rolling(lookback).max().to_numpy()
    prior_low = pd.Series(low).shift(1).rolling(lookback).min().to_numpy()
    return (close > prior_high).astype(int) - (close < prior_low).astype(int)


def ev_sides(p_long: np.ndarray, p_short: np.ndarray, cost_r: np.ndarray, margin: float = 0.1,
             reward_r: float = 2.0) -> np.ndarray:
    """Better side where expected R = p*(reward+1) - 1 - cost >= margin (non-wins counted as -1R)."""
    ev_long = p_long * (reward_r + 1) - 1 - cost_r
    ev_short = p_short * (reward_r + 1) - 1 - cost_r
    best = np.where(ev_long >= ev_short, 1, -1)
    return np.where(np.maximum(ev_long, ev_short) >= margin, best, 0)


def signals_from_sides(closes: np.ndarray, atr: np.ndarray, sides: np.ndarray, stop_mult: float,
                       target_mult: float, hold_seconds: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for close, a, side in zip(closes, atr, sides):
        if side == 0 or not np.isfinite(a) or a <= 0:
            out.append({})
            continue
        out.append(TradePlan(side=1 if side > 0 else -1, entry=0.0, stop=float(close - side * stop_mult * a),
                             tp1=float(close + side * target_mult * a), confidence_pct=100.0,
                             max_hold_seconds=hold_seconds).to_signal())
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_period(sides_by_symbol: Dict[str, np.ndarray], candles_by_symbol: Dict[str, pd.DataFrame],
                    atr_by_symbol: Dict[str, np.ndarray], bracket: Tuple[float, float],
                    period: Tuple[int, int], costs: CostModel, seeds: int,
                    eligible_by_symbol: Optional[Dict[str, np.ndarray]] = None) -> Dict[str, Any]:
    lo, hi = period
    model_trades: List[Dict[str, Any]] = []
    random_trades: List[Dict[str, Any]] = []
    for sym_idx, (symbol, candles) in enumerate(candles_by_symbol.items()):
        sel = ((candles["time"] >= lo) & (candles["time"] < hi)).to_numpy()
        if sel.sum() < 2:
            continue
        window = candles[sel].reset_index(drop=True)
        closes, atr = window["close"].to_numpy(float), atr_by_symbol[symbol][sel]
        eligible = eligible_by_symbol[symbol][sel] if eligible_by_symbol else None
        trades = evaluate_trades(window, signals_from_sides(closes, atr, sides_by_symbol[symbol][sel], *bracket,
                                                            HOLD_SECONDS), costs)
        model_trades += trades

        def random_trades_at(rate: float, seed) -> List[Dict[str, Any]]:
            sides = random_sides(len(window), rate, (seed, sym_idx, lo), eligible)
            return evaluate_trades(window, signals_from_sides(closes, atr, sides, *bracket, HOLD_SECONDS), costs)

        rate = matched_rate(len(trades), max(len(trades), 1) / len(window),
                            lambda r: len(random_trades_at(r, PROBE_SEED)))
        for seed in range(seeds):
            random_trades += random_trades_at(rate, seed)
    rand = summarize_trades(random_trades)
    rand["trade_count"] = rand["trade_count"] / max(seeds, 1)
    model = summarize_trades(model_trades)
    model["round_trip_cost_r"] = round_trip_cost_r(model_trades, costs)
    return {"model": model, "random": rand}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=365)
    parser.add_argument("--symbols", default=SYMBOLS)
    parser.add_argument("--first-train-days", type=float, default=120)
    parser.add_argument("--step-days", type=float, default=30)
    parser.add_argument("--holdout-days", type=float, default=60)
    parser.add_argument("--max-iter", type=int, default=300)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()

    import httpx

    now = time.time()
    start = int(now - args.days * 86400)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    async def load_all():
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await load_frames(client, symbols, (BASE, *CONTEXT), args.days + WARMUP_DAYS, now)

    datasets, candles_by_symbol, feats_by_symbol = [], {}, {}
    taker_rt = COSTS["taker"].round_trip
    for symbol, frames in asyncio.run(load_all()).items():
        feats = build_features(frames, base=BASE, context=CONTEXT)
        base = frames[BASE]
        atr_pct = feats["atr_pct"].to_numpy(float)
        long_win, short_win = barrier_labels(base["open"].to_numpy(float), base["high"].to_numpy(float),
                                             base["low"].to_numpy(float), tp=2 * atr_pct, sl=atr_pct,
                                             horizon=HORIZON)
        feats["symbol"], feats["y_long"], feats["y_short"] = symbol, long_win, short_win
        keep = (feats["time"] >= start).to_numpy()
        feats = feats[keep].reset_index(drop=True)
        candles = base[keep].reset_index(drop=True)
        feats_by_symbol[symbol], candles_by_symbol[symbol] = feats, candles
        datasets.append(feats)
        print(f"{symbol}: {len(feats)} bars, long base {np.nanmean(long_win[keep]):.3f} "
              f"short {np.nanmean(short_win[keep]):.3f}", file=sys.stderr)

    data = pd.concat(datasets).sort_values(["time", "symbol"]).reset_index(drop=True)
    data["p_long"], data["p_short"], data["vol_threshold"] = np.nan, np.nan, np.nan
    end = int(data["time"].max()) + BAR
    windows, holdout = walk_forward_schedule(start, end, args.first_train_days, args.step_days, args.holdout_days)
    times = data["time"].to_numpy(np.int64)
    X = data[COLUMNS].to_numpy(np.float32)
    labelled = data["y_long"].notna().to_numpy()

    for window in [*windows, holdout]:
        t0 = time.time()
        idx = np.flatnonzero(train_mask(times, window[0], LABEL_SECONDS) & labelled)
        model = SignalModel(max_iter=args.max_iter).fit(X[idx], data["y_long"].to_numpy()[idx],
                                                        data["y_short"].to_numpy()[idx])
        # Top 20% of volatility scores on the calibration slice, which the trees never saw.
        calib = idx[int(len(idx) * (1 - model.calib_fraction)):]
        pl_cal, ps_cal = model.predict(X[calib])
        threshold = float(np.quantile(pl_cal + ps_cal, 0.8))
        test = (times >= window[0]) & (times < window[1])
        p_long, p_short = model.predict(X[test])
        data.loc[test, "p_long"], data.loc[test, "p_short"] = p_long, p_short
        data.loc[test, "vol_threshold"] = threshold
        print(f"{'holdout' if window == holdout else 'window'} "
              f"{time.strftime('%Y-%m-%d', time.gmtime(window[0]))}: {time.time() - t0:.0f}s", file=sys.stderr)

    # Per-symbol side arrays aligned with each symbol's candles.
    sides: Dict[str, Dict[str, np.ndarray]] = {name: {} for name in BRACKETS}
    eligible: Dict[str, Dict[str, np.ndarray]] = {name: {} for name in BRACKETS}
    atr_by_symbol = {}
    for symbol, feats in feats_by_symbol.items():
        preds = data[data["symbol"] == symbol].set_index("time").reindex(feats["time"])
        candles = candles_by_symbol[symbol]
        atr_pct = feats["atr_pct"].to_numpy(float)
        atr_by_symbol[symbol] = atr_pct * candles["close"].to_numpy(float)
        p_long = preds["p_long"].to_numpy(float)
        p_short = preds["p_short"].to_numpy(float)
        has_pred = ~np.isnan(p_long)
        sides["A_pullback"][symbol] = pullback_sides(feats)
        eligible["A_pullback"][symbol] = np.isfinite(atr_pct)
        cost_r = taker_rt / np.where(atr_pct > 0, atr_pct, np.nan)
        sides["C_gbm"][symbol] = np.where(has_pred, ev_sides(np.nan_to_num(p_long), np.nan_to_num(p_short),
                                                             np.nan_to_num(cost_r, nan=np.inf)), 0)
        eligible["C_gbm"][symbol] = has_pred & np.isfinite(cost_r)
        vol_ok = has_pred & (np.nan_to_num(p_long + p_short) >= preds["vol_threshold"].to_numpy(float))
        eligible["B_breakout"][symbol] = vol_ok
        sides["B_breakout"][symbol] = np.where(vol_ok, breakout_sides(candles["high"].to_numpy(float),
                                                                      candles["low"].to_numpy(float),
                                                                      candles["close"].to_numpy(float)), 0)

    walk_forward = (windows[0][0], windows[-1][1])
    report: Dict[str, Any] = {
        "args": vars(args),
        "labels_walk_forward": label_metrics(data[(times >= walk_forward[0]) & (times < walk_forward[1])], 0.4),
        "labels_holdout": label_metrics(data[times >= holdout[0]], 0.4),
        "strategies": {},
    }
    for name, bracket in BRACKETS.items():
        for cost_name, costs in COSTS.items():
            block = {
                "windows": [evaluate_period(sides[name], candles_by_symbol, atr_by_symbol, bracket, w, costs,
                                            args.seeds, eligible[name]) for w in windows],
                "walk_forward": evaluate_period(sides[name], candles_by_symbol, atr_by_symbol, bracket,
                                                walk_forward, costs, args.seeds, eligible[name]),
                "holdout": evaluate_period(sides[name], candles_by_symbol, atr_by_symbol, bracket, holdout,
                                           costs, args.seeds, eligible[name]),
            }
            block["pass_checks"] = pass_checks(block["windows"], block["walk_forward"], block["holdout"],
                                               block["walk_forward"]["model"]["round_trip_cost_r"])
            # Taker costs decide; the maker-entry scenario is reported for information only.
            block["passed"] = all(block["pass_checks"].values()) if cost_name == "taker" else None
            report["strategies"][f"{name}/{cost_name}"] = block
            print(f"  evaluated {name} / {cost_name}", file=sys.stderr)

    out = REPORT_DIR / f"eval15m_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    print("\n" + report_header(34))
    for key, block in report["strategies"].items():
        print(f"{key}  (cost {block['walk_forward']['model']['round_trip_cost_r']:.3f}R, "
              f"windows +{positive_windows(block['windows'])}/{len(block['windows'])}) -> "
              f"{'info' if block['passed'] is None else 'PASS' if block['passed'] else 'FAIL'}")
        for period in ("walk_forward", "holdout"):
            print(report_line(f"  {period} model", block[period]["model"], 34))
            print(report_line(f"  {period} random", block[period]["random"], 34))
    for name in ("labels_walk_forward", "labels_holdout"):
        m = report[name]
        print(f"C {name}: AUC long {m['long']['auc']}, short {m['short']['auc']}, direction {m['direction_auc']}")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

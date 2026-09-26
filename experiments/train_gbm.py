"""Train and honestly evaluate the gradient-boosting entry signal.

For each symbol: history -> features (src/features.py) -> barrier labels
(src/barrier.py). All symbols are pooled. Walk-forward: train on everything
before each 30-day test window (labels purged so none overlaps the window),
predict the window. The last 60 days are a holdout, predicted once at the end.
Predictions become barrier signals (+1.2% / -0.6% / 2h) when the better side's
calibrated probability is at least the threshold, and are replayed through the
backtest evaluator with costs against random entries and flat.

Run:  python experiments/train_gbm.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.history import load_history  # noqa: E402
from src.barrier import barrier_labels  # noqa: E402
from src.config import (  # noqa: E402
    BARRIER_MAX_HOLD_SECONDS,
    BARRIER_SL_PCT,
    BARRIER_TP_PCT,
    BASE_DIR,
    MODELS_DIR,
)
from src.features import FEATURE_COLUMNS, build_features  # noqa: E402
from src.gbm_signal import SignalModel  # noqa: E402
from src.strategy_evaluator import CostModel, EvaluationConfig, evaluate_signal_sequence  # noqa: E402

DAY = 86400
BAR = 300
HORIZON = BARRIER_MAX_HOLD_SECONDS // BAR  # 24 bars of 5m
LABEL_SECONDS = (HORIZON + 1) * BAR  # entry bar + horizon
SYMBOLS = "BTCUSDT.P,ETHUSDT.P,SOLUSDT.P,BNBUSDT.P,XRPUSDT.P,DOGEUSDT.P,SUIUSDT.P,AVAXUSDT.P,LINKUSDT.P,LTCUSDT.P"
WARMUP_DAYS = 14  # 4h EMA50 needs ~9 days of closed 4h candles
BREAK_EVEN = (BARRIER_SL_PCT + 0.0012) / (BARRIER_TP_PCT + BARRIER_SL_PCT)


def walk_forward_schedule(start: int, end: int, first_train_days: float, step_days: float,
                          holdout_days: float) -> Tuple[List[Tuple[int, int]], Tuple[int, int]]:
    """Contiguous test windows after an initial training span, then the holdout."""
    holdout_start = int(end - holdout_days * DAY)
    windows, t = [], int(start + first_train_days * DAY)
    while t < holdout_start:
        windows.append((t, min(int(t + step_days * DAY), holdout_start)))
        t = windows[-1][1]
    # A stub shorter than half a step folds into the window before it.
    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < step_days * DAY / 2:
        windows[-2:] = [(windows[-2][0], windows[-1][1])]
    return windows, (holdout_start, int(end))


def subsample_mask(times: np.ndarray, every: int) -> np.ndarray:
    """Every Nth bar in time, for all symbols alike (rows are sorted by time, then symbol)."""
    return (times // BAR) % max(every, 1) == 0


def pass_checks(windows: List[Dict[str, Any]], walk_forward: Dict[str, Any], holdout: Dict[str, Any],
                round_trip_cost_r: float, min_holdout_trades: int = 30) -> Dict[str, bool]:
    """The spec's pass criteria. Every walk-forward window counts, traded or not."""
    positive = sum(1 for w in windows if w["model"]["trade_count"] and w["model"]["expectancy_r"] > 0)
    model, rand = walk_forward["model"], walk_forward["random"]
    return {
        "holdout_expectancy_positive": holdout["model"]["trade_count"] >= min_holdout_trades
            and holdout["model"]["expectancy_r"] > 0,
        "windows_positive_60pct": bool(windows) and positive / len(windows) >= 0.6,
        "beats_random_by_costs": model["trade_count"] > 0
            and model["expectancy_r"] - rand["expectancy_r"] > round_trip_cost_r,
    }


def train_mask(times: np.ndarray, test_start: int, label_seconds: int = LABEL_SECONDS) -> np.ndarray:
    """Rows whose label was fully known before ``test_start`` (no overlap with the test window)."""
    return times + label_seconds <= test_start


def _barrier_signal(close: float, side: int, probability: float) -> Dict[str, Any]:
    return {
        "action": "LONG" if side == 1 else "SHORT",
        "confidence_pct": round(probability * 100, 2),
        "stop_loss": close * (1 - side * BARRIER_SL_PCT),
        "take_profit_1": close * (1 + side * BARRIER_TP_PCT),
        "take_profit_2": 0.0,
        "max_hold_seconds": BARRIER_MAX_HOLD_SECONDS,
    }


def barrier_signals(closes: np.ndarray, p_long: np.ndarray, p_short: np.ndarray,
                    threshold: float) -> List[Dict[str, Any]]:
    """Signal the better side where its probability reaches ``threshold``; {} elsewhere."""
    out: List[Dict[str, Any]] = []
    for close, pl, ps in zip(closes, p_long, p_short):
        if max(pl, ps) < threshold:
            out.append({})
        elif pl >= ps:
            out.append(_barrier_signal(float(close), 1, float(pl)))
        else:
            out.append(_barrier_signal(float(close), -1, float(ps)))
    return out


def random_barrier_signals(closes: np.ndarray, rate: float, seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    return [_barrier_signal(float(c), rng.choice((1, -1)), 0.5) if rng.random() < rate else {} for c in closes]


def _frame(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["time", "open", "high", "low", "close", "volume", "taker_buy_volume"]]


async def load_symbol(client, symbol: str, days: float, now: float) -> Dict[str, pd.DataFrame]:
    return {tf: _frame(await load_history(client, symbol, tf, days + WARMUP_DAYS,
                                          BASE_DIR / "scratch" / "history", now=now, pause=0.3))
            for tf in ("5m", "15m", "1h", "4h")}


def symbol_dataset(symbol: str, frames: Dict[str, pd.DataFrame], start: int) -> pd.DataFrame:
    feats = build_features(frames)
    base = frames["5m"]
    long_win, short_win = barrier_labels(base["open"].to_numpy(float), base["high"].to_numpy(float),
                                         base["low"].to_numpy(float), BARRIER_TP_PCT, BARRIER_SL_PCT, HORIZON)
    feats["symbol"] = symbol
    feats["close"] = base["close"].to_numpy(float)
    feats["y_long"], feats["y_short"] = long_win, short_win
    return feats[feats["time"] >= start].reset_index(drop=True)


def _auc(y: np.ndarray, p: np.ndarray) -> Optional[float]:
    from sklearn.metrics import roc_auc_score

    ok = ~np.isnan(y)
    return float(roc_auc_score(y[ok], p[ok])) if 0 < y[ok].sum() < ok.sum() else None


def label_metrics(df: pd.DataFrame, threshold: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {"rows": int(len(df))}
    for side in ("long", "short"):
        y, p = df[f"y_{side}"].to_numpy(float), df[f"p_{side}"].to_numpy(float)
        sel = (p >= threshold) & ~np.isnan(y)
        out[side] = {
            "base_rate": float(np.nanmean(y)),
            "auc": _auc(y, p),
            "rows_at_threshold": int(sel.sum()),
            "hit_rate_at_threshold": float(y[sel].mean()) if sel.any() else None,
            "mean_p_at_threshold": float(p[sel].mean()) if sel.any() else None,
        }
        # Diagnostics only (not the decision rule): could any cut-off reach break-even?
        known = ~np.isnan(y)
        for name, q in (("top_1pct", 0.99), ("top_01pct", 0.999)):
            top = known & (p >= np.quantile(p[known], q))
            out[side][f"{name}_hit_rate"] = float(y[top].mean()) if top.any() else None
    # Direction skill without volatility: bars where exactly one side won.
    yl, ys = df["y_long"].to_numpy(float), df["y_short"].to_numpy(float)
    one = (yl + ys == 1)
    out["direction_auc"] = _auc(yl[one], (df["p_long"] - df["p_short"]).to_numpy(float)[one]) if one.any() else None
    return out


def _evaluate(candles: pd.DataFrame, signals: Sequence[Dict[str, Any]], costs: CostModel) -> Dict[str, Any]:
    config = EvaluationConfig(min_confidence_pct=0.0, risk_per_trade=0.01, max_leverage=50.0)
    rows = candles[["time", "open", "high", "low", "close", "volume"]].to_dict("records")
    result = evaluate_signal_sequence(rows, list(signals), config, costs)
    return {"trades": result["trades"], "trade_count": result["trade_count"]}


def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"trade_count": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0, "avg_cost_r": 0.0, "profit_factor": None}
    r = np.array([t["r_multiple"] for t in trades])
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "trade_count": len(trades),
        "win_rate_pct": float((r > 0).mean() * 100),
        "expectancy_r": float(r.mean()),
        "avg_cost_r": float(np.mean([t["cost_r"] for t in trades])),
        "profit_factor": float(gains / losses) if losses > 0 else None,
    }


def evaluate_period(data: pd.DataFrame, candles_by_symbol: Dict[str, pd.DataFrame], period: Tuple[int, int],
                    threshold: float, costs: CostModel, seeds: int) -> Dict[str, Any]:
    """Model trades and matched random trades over one period, per symbol and pooled."""
    lo, hi = period
    model_trades: List[Dict[str, Any]] = []
    random_trades: List[Dict[str, Any]] = []
    per_symbol = {}
    for symbol, candles in candles_by_symbol.items():
        rows = data[(data["symbol"] == symbol) & (data["time"] >= lo) & (data["time"] < hi)]
        if len(rows) < 2:
            continue
        window = candles[(candles["time"] >= lo) & (candles["time"] < hi)].reset_index(drop=True)
        preds = rows.set_index("time").reindex(window["time"])
        signals = barrier_signals(window["close"].to_numpy(float), preds["p_long"].fillna(0).to_numpy(),
                                  preds["p_short"].fillna(0).to_numpy(), threshold)
        trades = _evaluate(window, signals, costs)["trades"]
        model_trades += trades
        per_symbol[symbol] = summarize_trades(trades)
        # Random entries at the same trade count: rescale the entry rate against one probe seed.
        rate = max(len(trades), 1) / len(window)
        for _ in range(2):
            probe = _evaluate(window, random_barrier_signals(window["close"].to_numpy(float), rate, 10_000), costs)
            if probe["trade_count"]:
                rate = min(1.0, rate * max(len(trades), 1) / probe["trade_count"])
        for seed in range(seeds):
            random_trades += _evaluate(window, random_barrier_signals(window["close"].to_numpy(float), rate, seed),
                                       costs)["trades"]
    rand = summarize_trades(random_trades)
    rand["trade_count"] = rand["trade_count"] / max(seeds, 1)
    return {"model": summarize_trades(model_trades), "random": rand, "per_symbol": per_symbol}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=365)
    parser.add_argument("--symbols", default=SYMBOLS)
    parser.add_argument("--threshold", type=float, default=0.45)
    parser.add_argument("--first-train-days", type=float, default=120)
    parser.add_argument("--step-days", type=float, default=30)
    parser.add_argument("--holdout-days", type=float, default=60)
    parser.add_argument("--subsample", type=int, default=2, help="train on every Nth bar (neighbours overlap)")
    parser.add_argument("--max-iter", type=int, default=300)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()

    import httpx

    now = time.time()
    start = int(now - args.days * DAY)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    datasets, candles_by_symbol = [], {}

    async def load_all():
        async with httpx.AsyncClient(timeout=30.0) as client:
            return {s: await load_symbol(client, s, args.days, now) for s in symbols}

    for symbol, frames in asyncio.run(load_all()).items():
        ds = symbol_dataset(symbol, frames, start)
        datasets.append(ds)
        candles_by_symbol[symbol] = frames["5m"][frames["5m"]["time"] >= start].reset_index(drop=True)
        print(f"{symbol}: {len(ds)} bars, long base rate {np.nanmean(ds['y_long']):.3f}, "
              f"short {np.nanmean(ds['y_short']):.3f}", file=sys.stderr)
    data = pd.concat(datasets).sort_values(["time", "symbol"]).reset_index(drop=True)
    data["p_long"], data["p_short"] = np.nan, np.nan
    end = int(data["time"].max()) + BAR
    windows, holdout = walk_forward_schedule(start, end, args.first_train_days, args.step_days, args.holdout_days)
    times = data["time"].to_numpy(np.int64)
    X = data[FEATURE_COLUMNS].to_numpy(np.float32)

    def fit_before(test_start: int) -> SignalModel:
        mask = train_mask(times, test_start) & data["y_long"].notna().to_numpy()
        idx = np.flatnonzero(mask & subsample_mask(times, args.subsample))
        return SignalModel(max_iter=args.max_iter).fit(X[idx], data["y_long"].to_numpy()[idx],
                                                        data["y_short"].to_numpy()[idx])

    for window in [*windows, holdout]:
        t0 = time.time()
        model = fit_before(window[0])
        test = (times >= window[0]) & (times < window[1])
        p_long, p_short = model.predict(X[test])
        data.loc[test, "p_long"], data.loc[test, "p_short"] = p_long, p_short
        label = "holdout" if window == holdout else "window"
        print(f"{label} {time.strftime('%Y-%m-%d', time.gmtime(window[0]))}: fit+predict {time.time() - t0:.0f}s",
              file=sys.stderr)

    costs = CostModel(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    walk_forward = (windows[0][0], windows[-1][1])
    report: Dict[str, Any] = {
        "args": vars(args),
        "break_even_hit_rate": BREAK_EVEN,
        "features": FEATURE_COLUMNS,
        "labels_walk_forward": label_metrics(data[(times >= walk_forward[0]) & (times < walk_forward[1])], args.threshold),
        "labels_holdout": label_metrics(data[times >= holdout[0]], args.threshold),
        "windows": [],
    }
    for window in windows:
        result = evaluate_period(data, candles_by_symbol, window, args.threshold, costs, args.seeds)
        report["windows"].append({"start": window[0], "end": window[1], **result})
    report["walk_forward"] = evaluate_period(data, candles_by_symbol, walk_forward, args.threshold, costs, args.seeds)
    report["holdout"] = evaluate_period(data, candles_by_symbol, holdout, args.threshold, costs, args.seeds)

    wf, ho = report["walk_forward"], report["holdout"]
    positive_windows = sum(1 for w in report["windows"] if w["model"]["trade_count"] and w["model"]["expectancy_r"] > 0)
    traded_windows = len(report["windows"])
    # Full round trip in R, slippage included (avg_cost_r holds fees only).
    round_trip_cost_r = 2 * (args.fee_bps + args.slippage_bps) / 1e4 / BARRIER_SL_PCT
    checks = pass_checks(report["windows"], wf, ho, round_trip_cost_r)
    report["pass_checks"], report["passed"] = checks, all(checks.values())
    if report["passed"]:
        final = fit_before(end + LABEL_SECONDS)
        final.save(MODELS_DIR / "gbm_signal.joblib", metadata={"trained_until": end, "threshold": args.threshold,
                                                              "features": FEATURE_COLUMNS, "symbols": symbols})

    out = BASE_DIR / "scratch" / "backtests" / f"gbm_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    def line(name, s):
        pf = "-" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"
        return (f"{name:<22}{s['trade_count']:>8.0f}{s['win_rate_pct']:>7.1f}{s['expectancy_r']:>8.3f}"
                f"{pf:>6}{s['avg_cost_r']:>8.3f}")

    print(f"\n{'':<22}{'trades':>8}{'win%':>7}{'exp R':>8}{'PF':>6}{'cost R':>8}")
    for name, block in (("walk-forward", wf), ("holdout", ho)):
        print(line(f"{name} model", block["model"]))
        print(line(f"{name} random", block["random"]))
    for side in ("long", "short"):
        lw, lh = report["labels_walk_forward"][side], report["labels_holdout"][side]
        print(f"{side}: AUC walk-forward {lw['auc']}, holdout {lh['auc']}; hit rate at >= {args.threshold}: "
              f"{lw['hit_rate_at_threshold']} / {lh['hit_rate_at_threshold']} (break-even {BREAK_EVEN:.2f})")
    for name in ("labels_walk_forward", "labels_holdout"):
        m = report[name]
        print(f"{name}: direction AUC {m['direction_auc']}; top 1% hit long {m['long']['top_1pct_hit_rate']} "
              f"short {m['short']['top_1pct_hit_rate']}; top 0.1% long {m['long']['top_01pct_hit_rate']} "
              f"short {m['short']['top_01pct_hit_rate']}")
    print(f"windows positive: {positive_windows}/{traded_windows}")
    print(f"checks: {checks} -> {'PASS' if report['passed'] else 'FAIL'}")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

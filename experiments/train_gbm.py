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
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.harness import (  # noqa: E402
    BARRIER_BREAK_EVEN,
    DAY,
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
    summarize_trades,
    train_mask,
    walk_forward_schedule,
)
from src.barrier import barrier_labels  # noqa: E402
from src.config import BARRIER_MAX_HOLD_SECONDS, BARRIER_SL_PCT, BARRIER_TP_PCT, MODELS_DIR  # noqa: E402
from src.features import FEATURE_COLUMNS, build_features  # noqa: E402
from src.gbm_signal import SignalModel  # noqa: E402
from src.strategy_evaluator import CostModel  # noqa: E402
from src.trade_plan import TradePlan  # noqa: E402

BAR = 300
HORIZON = BARRIER_MAX_HOLD_SECONDS // BAR  # 24 bars of 5m
LABEL_SECONDS = (HORIZON + 1) * BAR  # entry bar + horizon
WARMUP_DAYS = 14  # 4h EMA50 needs ~9 days of closed 4h candles


def subsample_mask(times: np.ndarray, every: int) -> np.ndarray:
    """Every Nth bar in time, for all symbols alike (rows are sorted by time, then symbol)."""
    return (times // BAR) % max(every, 1) == 0


def _barrier_signal(close: float, side: int, probability: float) -> Dict[str, Any]:
    return TradePlan(side=side, entry=0.0, stop=close * (1 - side * BARRIER_SL_PCT),
                     tp1=close * (1 + side * BARRIER_TP_PCT), confidence_pct=round(probability * 100, 2),
                     max_hold_seconds=BARRIER_MAX_HOLD_SECONDS).to_signal()


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


def random_barrier_signals(closes: np.ndarray, rate: float, seed) -> List[Dict[str, Any]]:
    """Barrier signals on random bars and sides; ``seed`` may be a tuple: (seed, symbol index, period start)."""
    sides = random_sides(len(closes), rate, seed)
    return [_barrier_signal(float(c), int(side), 0.5) if side else {} for c, side in zip(closes, sides)]


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


def evaluate_period(data: pd.DataFrame, candles_by_symbol: Dict[str, pd.DataFrame], period: Tuple[int, int],
                    threshold: float, costs: CostModel, seeds: int) -> Dict[str, Any]:
    """Model trades and matched random trades over one period, per symbol and pooled."""
    lo, hi = period
    model_trades: List[Dict[str, Any]] = []
    random_trades: List[Dict[str, Any]] = []
    per_symbol = {}
    for sym_idx, (symbol, candles) in enumerate(candles_by_symbol.items()):
        rows = data[(data["symbol"] == symbol) & (data["time"] >= lo) & (data["time"] < hi)]
        if len(rows) < 2:
            continue
        window = candles[(candles["time"] >= lo) & (candles["time"] < hi)].reset_index(drop=True)
        preds = rows.set_index("time").reindex(window["time"])
        signals = barrier_signals(window["close"].to_numpy(float), preds["p_long"].fillna(0).to_numpy(),
                                  preds["p_short"].fillna(0).to_numpy(), threshold)
        trades = evaluate_trades(window, signals, costs)
        model_trades += trades
        per_symbol[symbol] = summarize_trades(trades)
        closes = window["close"].to_numpy(float)

        def random_trades_at(rate: float, seed) -> List[Dict[str, Any]]:
            return evaluate_trades(window, random_barrier_signals(closes, rate, (seed, sym_idx, lo)), costs)

        # Random entries at the same trade count.
        rate = matched_rate(len(trades), max(len(trades), 1) / len(window),
                            lambda r: len(random_trades_at(r, PROBE_SEED)))
        for seed in range(seeds):
            random_trades += random_trades_at(rate, seed)
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
            return await load_frames(client, symbols, ("5m", "15m", "1h", "4h"), args.days + WARMUP_DAYS, now)

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
        mask = train_mask(times, test_start, LABEL_SECONDS) & data["y_long"].notna().to_numpy()
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
        "break_even_hit_rate": BARRIER_BREAK_EVEN,
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
    # Full round trip in R, slippage included (avg_cost_r holds fees only).
    checks = pass_checks(report["windows"], wf, ho, costs.round_trip / BARRIER_SL_PCT)
    report["pass_checks"], report["passed"] = checks, all(checks.values())
    if report["passed"]:
        final = fit_before(end + LABEL_SECONDS)
        final.save(MODELS_DIR / "gbm_signal.joblib", metadata={"trained_until": end, "threshold": args.threshold,
                                                              "features": FEATURE_COLUMNS, "symbols": symbols})

    out = REPORT_DIR / f"gbm_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    print("\n" + report_header(22, cost=True))
    for name, block in (("walk-forward", wf), ("holdout", ho)):
        print(report_line(f"{name} model", block["model"], 22, cost=True))
        print(report_line(f"{name} random", block["random"], 22, cost=True))
    for side in ("long", "short"):
        lw, lh = report["labels_walk_forward"][side], report["labels_holdout"][side]
        print(f"{side}: AUC walk-forward {lw['auc']}, holdout {lh['auc']}; hit rate at >= {args.threshold}: "
              f"{lw['hit_rate_at_threshold']} / {lh['hit_rate_at_threshold']} (break-even {BARRIER_BREAK_EVEN:.2f})")
    for name in ("labels_walk_forward", "labels_holdout"):
        m = report[name]
        print(f"{name}: direction AUC {m['direction_auc']}; top 1% hit long {m['long']['top_1pct_hit_rate']} "
              f"short {m['short']['top_1pct_hit_rate']}; top 0.1% long {m['long']['top_01pct_hit_rate']} "
              f"short {m['short']['top_01pct_hit_rate']}")
    print(f"windows positive: {positive_windows(report['windows'])}/{len(report['windows'])}")
    print(f"checks: {checks} -> {'PASS' if report['passed'] else 'FAIL'}")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

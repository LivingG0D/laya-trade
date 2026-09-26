"""Backtest of the live auto-trader decision path against simple baselines.

For every closed 5m bar over the last ``--days`` days it rebuilds the exact
state the auto-trader decides on (last 150 closed candles per timeframe,
15m/1h context only once those candles have closed), runs the decision
engines, turns decisions into signals with the live SignalGenerator, and
replays them through `evaluate_signal_sequence` with costs and the live
break-even and opposing-signal exits.

Run:  python experiments/strategy_baseline.py                     # defaults
      python experiments/strategy_baseline.py --engines fallback --days 7

It re-fits nothing. Results are in R (multiples of the risk per trade).
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from experiments.history import INTERVAL_SECONDS, load_history  # noqa: E402
from src.auto_trader import merge_timeframes, timeframe_snapshot  # noqa: E402
from src.config import BASE_DIR  # noqa: E402
from src.signal_generator import SignalGenerator  # noqa: E402
from src.strategy_evaluator import (  # noqa: E402
    CostModel,
    EvaluationConfig,
    evaluate_signal_sequence,
    walk_forward_windows,
)

ENTRY_TF = "5m"
CONTEXT_TFS = ("15m", "1h")
WINDOW = 150  # candles per timeframe, as the live auto-trader fetches
WARMUP_DAYS = 7  # enough 1h history to fill the first window
SCRATCH = BASE_DIR / "scratch"
HOLD = {"position": "NEUTRAL_HOLD"}
KNOWN_GAPS = [
    "Binance futures prices stand in for Toobit.",
    "Funding costs are 0.",
    "Sizing is fixed-risk; live uses fixed margin. Compare results in R.",
    "No order-book or funding context (the live auto-trader does not fetch them either).",
    "Batched model inference can differ from single calls in the last decimals.",
]


def htf_window(candles: Sequence[Mapping[str, Any]], opens: Sequence[int], step: int,
               bar_close: int, size: int = WINDOW) -> List[Mapping[str, Any]]:
    """The last ``size`` candles whose close time is at or before ``bar_close``."""
    closed = bisect.bisect_right(opens, bar_close - step)
    return list(candles[max(0, closed - size):closed])


def _decision(position: str) -> Dict[str, Any]:
    return {"position": position, "directional_conviction": 0.99, "engine": "baseline"}


def random_decisions(count: int, rate: float, seed: int) -> List[Dict[str, Any]]:
    """Enter at ``rate`` of bars in a random direction; everything else holds."""
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        if rng.random() < rate:
            out.append(_decision("STRONG_LONG" if rng.random() < 0.5 else "STRONG_SHORT"))
        else:
            out.append(HOLD)
    return out


def tradeable_rate(signals: Sequence[Mapping[str, Any]], min_confidence: float) -> float:
    """Share of bars whose signal is directional and passes the confidence gate."""
    hits = sum(1 for s in signals if s.get("action") in ("LONG", "SHORT")
               and float(s.get("confidence_pct", 0.0)) >= min_confidence)
    return hits / len(signals) if signals else 0.0


def ema_cross_decisions(states: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Long when EMA9 crosses above EMA21, short when it crosses below."""
    out: List[Dict[str, Any]] = []
    prev: Optional[float] = None
    for state in states:
        fast, slow = state.get("ema9"), state.get("ema21")
        diff = None if fast is None or slow is None else fast - slow
        if prev is not None and diff is not None and prev <= 0 < diff:
            out.append(_decision("STRONG_LONG"))
        elif prev is not None and diff is not None and prev >= 0 > diff:
            out.append(_decision("STRONG_SHORT"))
        else:
            out.append(HOLD)
        prev = diff
    return out


def build_states(history: Dict[str, List[Dict[str, Any]]], symbol: str, start: int,
                 leverage: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Auto-trader states for every entry bar opening at or after ``start``."""
    entry = history[ENTRY_TF]
    entry_step = INTERVAL_SECONDS[ENTRY_TF]
    opens = {tf: [c["time"] for c in history[tf]] for tf in CONTEXT_TFS}
    memo: Dict[tuple, Dict[str, Any]] = {}
    candles, states = [], []
    for i, bar in enumerate(entry):
        if bar["time"] < start or i + 1 < WINDOW:
            continue
        bar_close = bar["time"] + entry_step
        snapshots = {ENTRY_TF: timeframe_snapshot(entry[i + 1 - WINDOW:i + 1], symbol, ENTRY_TF, leverage)}
        for tf in CONTEXT_TFS:
            window = htf_window(history[tf], opens[tf], INTERVAL_SECONDS[tf], bar_close)
            if len(window) < WINDOW:
                break
            key = (tf, window[-1]["time"])
            if key not in memo:
                memo[key] = timeframe_snapshot(window, symbol, tf, leverage)
            snapshots[tf] = memo[key]
        else:
            candles.append(bar)
            states.append(merge_timeframes(snapshots))
    return candles, states


def model_decisions(engine, states: List[Dict[str, Any]], cache_path: Path) -> List[Dict[str, Any]]:
    """Model-only decisions, cached by the hash of the exact text the model sees."""
    cache: Dict[str, Dict[str, Any]] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    keys = [hashlib.sha256(engine._agent_summary(s).encode()).hexdigest() for s in states]
    missing = [i for i, k in enumerate(keys) if k not in cache]
    chunk = 512
    for n in range(0, len(missing), chunk):
        idx = missing[n:n + chunk]
        for i, decision in zip(idx, engine._agent_decisions([states[i] for i in idx])):
            cache[keys[i]] = decision
        print(f"    model {min(n + chunk, len(missing))}/{len(missing)}", file=sys.stderr)
        cache_path.write_text(json.dumps(cache))
    return [cache[k] for k in keys]


def evaluate(candles, signals, config: EvaluationConfig, costs: CostModel, window_bars: int) -> Dict[str, Any]:
    full = evaluate_signal_sequence(candles, signals, config, costs)
    windows = []
    for w in walk_forward_windows(len(candles), window_bars, window_bars):
        if w["test_end"] - w["test_start"] < 2:
            continue
        r = evaluate_signal_sequence(candles[w["test_start"]:w["test_end"]],
                                     signals[w["test_start"]:w["test_end"]], config, costs)
        windows.append(r["expectancy_r"] if r["trade_count"] else None)
    traded = [w for w in windows if w is not None]
    row = {k: full[k] for k in ("trade_count", "win_rate_pct", "expectancy_r", "profit_factor",
                                "max_drawdown_pct", "avg_cost_r", "return_pct")}
    row["windows"] = len(windows)
    row["windows_positive"] = sum(1 for w in traded if w > 0)
    row["window_expectancy_r"] = windows
    exits: Dict[str, int] = {}
    for trade in full["trades"]:
        exits[trade["exit_reason"]] = exits.get(trade["exit_reason"], 0) + 1
    row["exit_reasons"] = exits
    return row


def average_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for key in ("trade_count", "win_rate_pct", "expectancy_r", "max_drawdown_pct", "avg_cost_r",
                "return_pct", "windows", "windows_positive"):
        out[key] = mean(r[key] for r in rows)
    pfs = [r["profit_factor"] for r in rows if r["profit_factor"] is not None]
    out["profit_factor"] = mean(pfs) if pfs else None
    out["expectancy_r_range"] = [min(r["expectancy_r"] for r in rows), max(r["expectancy_r"] for r in rows)]
    return out


def print_table(rows: List[Dict[str, Any]]) -> None:
    head = f"{'symbol':<11}{'strategy':<14}{'trades':>7}{'win%':>7}{'exp R':>8}{'PF':>6}{'maxDD%':>8}{'cost R':>8}{'win windows':>13}"
    print(head)
    print("-" * len(head))
    for r in rows:
        pf = "-" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
        print(f"{r['symbol']:<11}{r['strategy']:<14}{r['trade_count']:>7.0f}{r['win_rate_pct']:>7.1f}"
              f"{r['expectancy_r']:>8.3f}{pf:>6}{r['max_drawdown_pct']:>8.1f}{r['avg_cost_r']:>8.3f}"
              f"{r['windows_positive']:>7.0f}/{r['windows']:<5.0f}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=90)
    parser.add_argument("--symbols", default="BTCUSDT.P,ETHUSDT.P,SOLUSDT.P")
    parser.add_argument("--engines", default="live,model,fallback")
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--min-confidence", type=float, default=65.0)
    parser.add_argument("--leverage", type=int, default=50)
    parser.add_argument("--window-days", type=float, default=14)
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args()

    engines = [e for e in args.engines.split(",") if e]
    costs = CostModel(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    live_exits = dict(breakeven_trigger_pct=0.09 / args.leverage, opposing_exit_confidence=70.0,
                      max_leverage=float(args.leverage), risk_per_trade=0.01)
    strategy_cfg = EvaluationConfig(min_confidence_pct=args.min_confidence, **live_exits)
    baseline_cfg = EvaluationConfig(min_confidence_pct=0.0, **live_exits)
    window_bars = int(args.window_days * 86400 / INTERVAL_SECONDS[ENTRY_TF])

    from src.laya_engine import LayaEngine

    engine = LayaEngine()
    if {"live", "model"} & set(engines):
        if not await engine.start_server() or engine._agent is None:
            print("Native Laya model did not load; running fallback only.", file=sys.stderr)
            engines = [e for e in engines if e == "fallback"]

    now = time.time()
    start = int(now - args.days * 86400)
    rows: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            print(f"{symbol}: loading history", file=sys.stderr)
            history = {
                tf: await load_history(client, symbol, tf, args.days + WARMUP_DAYS, SCRATCH / "history", now=now)
                for tf in (ENTRY_TF, *CONTEXT_TFS)
            }
            candles, states = build_states(history, symbol, start, args.leverage)
            print(f"{symbol}: {len(states)} bars", file=sys.stderr)

            fallback = [engine._fallback_decision(s, 0.0) for s in states]
            decisions: Dict[str, List[Dict[str, Any]]] = {}
            if "fallback" in engines:
                decisions["fallback"] = fallback
            if {"live", "model"} & set(engines):
                cache = SCRATCH / "backtest_cache" / f"{engine.model_filename}_{symbol.replace('.', '_')}.json"
                cache.parent.mkdir(parents=True, exist_ok=True)
                model = model_decisions(engine, states, cache)
                if "model" in engines:
                    decisions["model"] = model
                if "live" in engines:
                    decisions["live"] = [engine._blend(m, f) for m, f in zip(model, fallback)]

            generator = SignalGenerator(leverage=args.leverage)

            def to_signals(ds):
                return [generator.generate_signal(s, d) for s, d in zip(states, ds)]

            directional_rate, target_trades = 0.0, 0
            for name, ds in decisions.items():
                signals = to_signals(ds)
                row = evaluate(candles, signals, strategy_cfg, costs, window_bars)
                if name == engines[0]:
                    directional_rate = tradeable_rate(signals, args.min_confidence)
                    target_trades = row["trade_count"]
                rows.append({"symbol": symbol, "strategy": name, **row})

            # Strategy signals cluster on consecutive bars and filters drop some random
            # entries, so matching signal rate does not match trade count. Rescale the
            # random entry rate twice against a probe seed to match the strategy's trades.
            random_rate = directional_rate
            for _ in range(2):
                probe = evaluate(candles, to_signals(random_decisions(len(states), random_rate, 10_000)),
                                 baseline_cfg, costs, window_bars)["trade_count"]
                if not probe or not target_trades:
                    break
                random_rate = min(1.0, random_rate * target_trades / probe)
            rows.append({"symbol": symbol, "strategy": "flat",
                         **evaluate(candles, [{}] * len(candles), baseline_cfg, costs, window_bars)})
            seeds = [evaluate(candles, to_signals(random_decisions(len(states), random_rate, seed)),
                              baseline_cfg, costs, window_bars) for seed in range(args.seeds)]
            rows.append({"symbol": symbol, "strategy": "random", "directional_rate": directional_rate,
                         **average_rows(seeds)})
            rows.append({"symbol": symbol, "strategy": "ema_cross",
                         **evaluate(candles, to_signals(ema_cross_decisions(states)), baseline_cfg, costs, window_bars)})

    await engine.close()
    out_dir = SCRATCH / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"baseline_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps({"args": vars(args), "engines": engines, "known_gaps": KNOWN_GAPS,
                                    "rows": rows}, indent=2, default=str))
    print_table(rows)
    print(f"\nrandom = entries matched to the first engine's trade count, same bracket, mean of {args.seeds} seeds")
    print("Known gaps: " + " ".join(KNOWN_GAPS))
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

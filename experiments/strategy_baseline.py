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
import json
import os
import random
import sys
import time
from dataclasses import replace
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from experiments.history import INTERVAL_SECONDS, load_history  # noqa: E402
from src.market_snapshot import WINDOW, closed_window, merge_timeframes, timeframe_snapshot  # noqa: E402
from src.config import BASE_DIR  # noqa: E402
from src.edge_gate import EdgeTable, gate_walk_forward, save_tables, signal_outcomes  # noqa: E402
from src.signal_generator import SignalGenerator  # noqa: E402
from src.trade_management import ExitPolicy  # noqa: E402
from src.strategy_evaluator import (  # noqa: E402
    CostModel,
    EvaluationConfig,
    evaluate_signal_sequence,
    walk_forward_windows,
)

ENTRY_TF = "5m"
CONTEXT_TFS = ("15m", "1h")
WARMUP_DAYS = 7  # enough 1h history to fill the first window
SCRATCH = BASE_DIR / "scratch"
HOLD = {"position": "NEUTRAL_HOLD"}
KNOWN_GAPS = [
    "Binance futures prices stand in for Toobit.",
    "Funding costs are 0.",
    "Sizing is fixed-risk; live uses fixed margin. Compare results in R.",
    "No order-book or funding context (the live auto-trader does not fetch them either).",
    "Batched model inference can differ from single calls in the last decimals.",
    "Decision latency is not simulated: live decides a few seconds after the bar closes.",
]


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
    opens = {tf: [c["time"] for c in history[tf]] for tf in (ENTRY_TF, *CONTEXT_TFS)}
    memo: Dict[tuple, Dict[str, Any]] = {}
    candles, states = [], []
    for i, bar in enumerate(entry):
        if bar["time"] < start or i + 1 < WINDOW:
            continue
        # The state the live auto-trader builds when this bar closes (src/market_snapshot.py).
        bar_close = bar["time"] + entry_step
        window = closed_window(entry, ENTRY_TF, bar_close, opens=opens[ENTRY_TF])
        snapshots = {ENTRY_TF: timeframe_snapshot(window, symbol, ENTRY_TF, leverage)}
        for tf in CONTEXT_TFS:
            window = closed_window(history[tf], tf, bar_close, opens=opens[tf])
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


def warm_agent_cache(engine, states: List[Dict[str, Any]], cache_path: Path) -> Dict[str, Dict[str, Any]]:
    """Native-model decisions for every state, cached on disk by the hash of the exact text the model sees."""
    cache: Dict[str, Dict[str, Any]] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    chunk = 512
    for n in range(0, len(states), chunk):
        before = len(cache)
        engine.agent_decisions(states[n:n + chunk], cache)
        if len(cache) > before:
            print(f"    model {min(n + chunk, len(states))}/{len(states)}", file=sys.stderr)
            cache_path.write_text(json.dumps(cache))
    return cache


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
    parser.add_argument("--breakeven", choices=("legacy", "tp1_fees", "off"), default="legacy",
                        help="legacy: 9%% ROE or TP1 -> entry+0.05%%; tp1_fees: TP1 only -> entry+costs")
    parser.add_argument("--exit-profile", choices=("legacy", "barrier"), default="legacy")
    parser.add_argument("--gate", action="store_true",
                        help="add '<engine>+gate' rows: walk-forward outcome gate instead of the confidence threshold")
    parser.add_argument("--gate-min-samples", type=int, default=30)
    parser.add_argument("--max-cost-share", type=float, default=None,
                        help="reject trades whose round-trip costs exceed this share of the risk")
    args = parser.parse_args()

    engines = [e for e in args.engines.split(",") if e]
    costs = CostModel(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    # The auto-trader's exit rules, built from the settings keys it reads.
    exits = ExitPolicy.from_settings({"breakeven_mode": args.breakeven, "leverage": args.leverage})
    sizing = dict(max_leverage=float(args.leverage), risk_per_trade=0.01)
    strategy_cfg = EvaluationConfig(min_confidence_pct=args.min_confidence, exits=exits, **sizing)
    baseline_cfg = EvaluationConfig(min_confidence_pct=0.0, exits=exits, **sizing)
    # Outcome labels: each signal alone, no confidence gate, no opposing exit.
    outcome_cfg = EvaluationConfig(min_confidence_pct=0.0, exits=replace(exits, opposing_min_confidence=None), **sizing)
    # Gated signals already passed the outcome gate: any opposite gated signal closes the trade.
    gated_cfg = EvaluationConfig(min_confidence_pct=0.0, exits=replace(exits, opposing_min_confidence=0.0), **sizing)
    horizon_bars = 288  # one day of 5m bars caps an outcome label
    edge_tables: Dict[str, Dict[str, EdgeTable]] = {}
    window_bars = int(args.window_days * 86400 / INTERVAL_SECONDS[ENTRY_TF])

    from src.laya_engine import LayaEngine

    engine = LayaEngine()
    if {"live", "model"} & set(engines):
        await engine.start_server()
        if engine.runtime == "fallback":
            print("No Laya model loaded; running fallback only.", file=sys.stderr)
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

            decisions: Dict[str, List[Dict[str, Any]]] = {}
            if "fallback" in engines:
                decisions["fallback"] = [engine.fallback_decision(s, 0.0) for s in states]
            if {"live", "model"} & set(engines):
                cache: Dict[str, Dict[str, Any]] = {}
                if engine.runtime == "agent":
                    cache_path = SCRATCH / "backtest_cache" / f"{engine.model_filename}_{symbol.replace('.', '_')}.json"
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache = warm_agent_cache(engine, states, cache_path)
                # The live row runs the engine's public path, exactly what the auto-trader calls.
                live = await engine.decide_batch(states, agent_cache=cache)
                if "model" in engines:  # model only: no blend (the ONNX runtime never blends)
                    decisions["model"] = engine.agent_decisions(states, cache) if engine.runtime == "agent" else live
                if "live" in engines:
                    decisions["live"] = live

            generator = SignalGenerator(leverage=args.leverage, exit_profile=args.exit_profile,
                                        max_cost_share=args.max_cost_share)

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
                if args.gate:
                    outcomes = signal_outcomes(candles, signals, outcome_cfg, costs, horizon_bars)
                    gated = gate_walk_forward(signals, outcomes, window_bars, horizon_bars, args.gate_min_samples)
                    rows.append({"symbol": symbol, "strategy": f"{name}+gate",
                                 **evaluate(candles, gated, gated_cfg, costs, window_bars)})
                    edge_tables.setdefault(name, {})[symbol] = EdgeTable.fit(signals, outcomes, args.gate_min_samples)

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
    if edge_tables:
        table_dir = SCRATCH / "edge_tables"
        table_dir.mkdir(parents=True, exist_ok=True)
        for name, per_symbol in edge_tables.items():
            path = table_dir / f"{name}_{args.exit_profile}_{args.breakeven}.json"
            save_tables(path, per_symbol)
            print(f"Edge table (fit on all {args.days:g} days): {path}")
    print_table(rows)
    print(f"\nrandom = entries matched to the first engine's trade count, same bracket, mean of {args.seeds} seeds")
    print("Known gaps: " + " ".join(KNOWN_GAPS))
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

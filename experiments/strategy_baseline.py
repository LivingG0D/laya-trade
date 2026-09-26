"""Reproducible expectancy baseline for the AutoTrader entry/exit geometry.

Fetches real historical candles, runs the real indicator pipeline and the real
Laya decision + signal generator over every bar, then replays the resulting
signal sequence through `evaluate_signal_sequence` with explicit costs.

Run:  python experiments/strategy_baseline.py --bars 150 --interval 5m

This is the honest test of the strategy's mechanics. It does NOT re-fit
anything: it measures the R multiple actually produced by the configured
stop/target geometry against the real signal stream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators import compute_all_indicators  # noqa: E402
from src.laya_engine import LayaEngine  # noqa: E402
from src.market_data import MarketDataService  # noqa: E402
from src.signal_generator import SignalGenerator  # noqa: E402
from src.strategy_evaluator import (  # noqa: E402
    CostModel,
    EvaluationConfig,
    evaluate_signal_sequence,
)

SYMBOLS = ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"]


async def build_signal_series(
    market: MarketDataService, symbol: str, interval: str, bars: int, leverage: float
) -> Dict[str, Any]:
    candles = await market.fetch_klines(symbol=symbol, interval=interval, limit=bars)
    if not candles:
        raise RuntimeError(f"no candles returned for {symbol}")

    # One indicator pass over the whole series, then slice per bar.
    computed = compute_all_indicators(candles)
    series: List[Dict[str, Any]] = computed["series"]
    series = series[-bars:]
    candles = candles[-bars:]

    engine = LayaEngine()
    generator = SignalGenerator()

    signals: List[Dict[str, Any]] = []
    decision_latencies: List[float] = []
    for index, state in enumerate(series):
        state = dict(state)
        state["symbol"] = symbol
        state["interval"] = interval
        state["leverage"] = leverage
        state["is_closed"] = True
        started = time.time()
        try:
            decision = await engine.decide(state)
        except Exception as exc:  # noqa: BLE001
            print(f"  decision failed at {index}: {exc}", file=sys.stderr)
            decision = {}
        decision_latencies.append(time.time() - started)
        signals.append(generator.generate_signal(state, decision))
        if index and index % 25 == 0:
            print(f"  {symbol} {index}/{len(series)} signals", file=sys.stderr)

    realized_rr: List[float] = []
    for signal in signals:
        entry = float(signal.get("entry_price", 0) or 0)
        stop = float(signal.get("stop_loss", 0) or 0)
        tp1 = float(signal.get("take_profit_1", 0) or 0)
        if entry > 0 and stop > 0 and tp1 > 0 and stop != entry:
            risk = abs(entry - stop)
            reward = abs(tp1 - entry)
            if risk > 0:
                realized_rr.append(reward / risk)

    return {
        "symbol": symbol,
        "interval": interval,
        "candles": candles,
        "signals": signals,
        "realized_rr": realized_rr,
        "decision_latencies": decision_latencies,
    }


def summarize(bundle: Dict[str, Any], config: EvaluationConfig, costs: CostModel) -> Dict[str, Any]:
    result = evaluate_signal_sequence(
        bundle["candles"], bundle["signals"], config=config, costs=costs
    )
    rr = bundle["realized_rr"]
    directional = [s for s in bundle["signals"] if str(s.get("action", "")).upper() != "NEUTRAL"]
    summary = {
        "symbol": bundle["symbol"],
        "interval": bundle["interval"],
        "bars": len(bundle["candles"]),
        "directional_signals": len(directional),
        "directional_pct": round(100.0 * len(directional) / max(1, len(bundle["signals"])), 1),
        "mean_target_rr": round(sum(rr) / len(rr), 3) if rr else None,
        "trades": result.get("trade_count", 0),
        "win_rate_pct": result.get("win_rate_pct"),
        "expectancy_r": result.get("expectancy_r"),
        "profit_factor": result.get("profit_factor"),
        "net_profit": result.get("net_profit"),
        "return_pct": result.get("return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "skipped_low_confidence": result.get("skipped_low_confidence"),
        "skipped_invalid_signals": result.get("skipped_invalid_signals"),
    }
    return summary


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=150)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--leverage", type=float, default=10.0)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--min-confidence", type=float, default=70.0)
    args = parser.parse_args()

    costs = CostModel(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, funding_bps_per_bar=0.0)
    market = MarketDataService()
    rows = []
    for symbol in SYMBOLS:
        print(f"building signals for {symbol} ({args.bars} x {args.interval})...", file=sys.stderr)
        bundle = await build_signal_series(market, symbol, args.interval, args.bars, args.leverage)
        for threshold in (0.0, args.min_confidence):
            config = EvaluationConfig(
                initial_equity=1000.0,
                risk_per_trade=0.01,
                max_leverage=args.leverage,
                min_confidence_pct=threshold,
            )
            row = summarize(bundle, config, costs)
            row["min_confidence_pct"] = threshold
            rows.append(row)
            print(json.dumps(row), file=sys.stderr)
    await market.close()

    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

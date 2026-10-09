"""Outcome-based trade gate.

Each directional signal is scored by the R it would have produced if taken on
its own. Signals are grouped by (side, 5-point confidence bucket); a bucket is
tradeable only when its mean R, measured on past signals, is positive with
enough samples. This replaces an uncalibrated confidence threshold with one
learned from outcomes after costs and the actual exit rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .strategy_evaluator import CostModel, EvaluationConfig, evaluate_signal_sequence, walk_forward_windows
from .trade_plan import TradePlan

BUCKET_WIDTH = 5.0


def bucket_key(signal: Mapping[str, Any]) -> Optional[str]:
    plan = TradePlan.from_signal(signal)
    if plan is None:
        return None
    return f"{plan.action}:{int(plan.confidence_pct // BUCKET_WIDTH * BUCKET_WIDTH)}"


def signal_outcomes(candles: Sequence[Mapping[str, Any]], signals: Sequence[Mapping[str, Any]],
                    config: EvaluationConfig, costs: CostModel, horizon_bars: int) -> List[Optional[float]]:
    """R of each directional signal traded alone over at most ``horizon_bars`` bars (None if no trade)."""
    outcomes: List[Optional[float]] = []
    for i, signal in enumerate(signals):
        window = list(candles[i:i + horizon_bars + 2])
        if bucket_key(signal) is None or len(window) < 2:
            outcomes.append(None)
            continue
        result = evaluate_signal_sequence(window, [signal] + [{}] * (len(window) - 1), config, costs)
        outcomes.append(result["trades"][0]["r_multiple"] if result["trades"] else None)
    return outcomes


@dataclass
class EdgeTable:
    stats: Dict[str, Dict[str, float]] = field(default_factory=dict)
    min_samples: int = 30

    @classmethod
    def fit(cls, signals: Sequence[Mapping[str, Any]], outcomes: Sequence[Optional[float]],
            min_samples: int = 30) -> "EdgeTable":
        sums: Dict[str, List[float]] = {}
        for signal, outcome in zip(signals, outcomes):
            key = bucket_key(signal) if signal else None
            if key is not None and outcome is not None:
                sums.setdefault(key, []).append(outcome)
        stats = {k: {"n": len(v), "mean_r": sum(v) / len(v)} for k, v in sums.items()}
        return cls(stats=stats, min_samples=min_samples)

    def expected_r(self, signal: Mapping[str, Any]) -> Optional[float]:
        row = self.stats.get(bucket_key(signal) or "")
        if not row or row["n"] < self.min_samples:
            return None
        return row["mean_r"]

    def allows(self, signal: Mapping[str, Any]) -> bool:
        expected = self.expected_r(signal)
        return expected is not None and expected > 0



def save_tables(path: Path, tables: Mapping[str, EdgeTable]) -> None:
    """Write one table per symbol: the file the backtest writes and the auto-trader reads."""
    Path(path).write_text(json.dumps(
        {symbol: {"min_samples": t.min_samples, "stats": t.stats} for symbol, t in tables.items()}, indent=2))


def load_tables(path: Path) -> Dict[str, EdgeTable]:
    data = json.loads(Path(path).read_text())
    return {symbol: EdgeTable(stats=row["stats"], min_samples=int(row["min_samples"])) for symbol, row in data.items()}


def gate_walk_forward(signals: Sequence[Mapping[str, Any]], outcomes: Sequence[Optional[float]],
                      window_bars: int, horizon_bars: int, min_samples: int = 30) -> List[Mapping[str, Any]]:
    """Gate each test window with a table fit only on outcomes resolved before it starts.

    A signal at bar i needs bars up to i + 1 + horizon_bars to resolve, so only
    signals with i <= start - horizon_bars - 1 inform the window starting at
    ``start``. The first window is training only and never trades.
    """
    gated: List[Mapping[str, Any]] = [{} for _ in signals]
    for window in walk_forward_windows(len(signals), window_bars, window_bars):
        cutoff = max(0, window["test_start"] - horizon_bars)
        table = EdgeTable.fit(signals[:cutoff], outcomes[:cutoff], min_samples)
        for i in range(window["test_start"], window["test_end"]):
            if signals[i] and table.allows(signals[i]):
                gated[i] = signals[i]
    return gated

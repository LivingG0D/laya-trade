"""Research harness: the evaluation every phase script runs its rule through.

A phase script supplies its rule (signals, or its own simulator for exits the
evaluator does not model) and gets the rest from here: history loading,
walk-forward windows with a holdout, label purging, random entries matched to
the rule's trade count, trade summaries in R, the full round-trip cost in R,
the spec's pass checks and the report lines. Costs come from one CostModel.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.history import load_history  # noqa: E402
from src.config import BARRIER_SL_PCT, BARRIER_TP_PCT, BASE_DIR, ROUND_TRIP_COST_PCT  # noqa: E402
from src.strategy_evaluator import CostModel, EvaluationConfig, evaluate_signal_sequence  # noqa: E402

DAY = 86400
SYMBOLS = "BTCUSDT.P,ETHUSDT.P,SOLUSDT.P,BNBUSDT.P,XRPUSDT.P,DOGEUSDT.P,SUIUSDT.P,AVAXUSDT.P,LINKUSDT.P,LTCUSDT.P"
HISTORY_DIR = BASE_DIR / "scratch" / "history"
REPORT_DIR = BASE_DIR / "scratch" / "backtests"
PROBE_SEED = 10_000  # seed of the probe run that tunes the random entry rate
# Hit rate a +1.2% / -0.6% barrier trade needs to break even after round-trip costs.
BARRIER_BREAK_EVEN = (BARRIER_SL_PCT + ROUND_TRIP_COST_PCT) / (BARRIER_TP_PCT + BARRIER_SL_PCT)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def frame(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["time", "open", "high", "low", "close", "volume", "taker_buy_volume"]]


async def load_candles(client, symbols: Sequence[str], timeframes: Sequence[str], days: float, now: float,
                       pause: float = 0.3) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Closed candles per symbol and timeframe, from the disk cache plus whatever is missing."""
    return {symbol: {tf: await load_history(client, symbol, tf, days, HISTORY_DIR, now=now, pause=pause)
                     for tf in timeframes}
            for symbol in symbols}


async def load_frames(client, symbols: Sequence[str], timeframes: Sequence[str], days: float, now: float,
                      pause: float = 0.3) -> Dict[str, Dict[str, pd.DataFrame]]:
    loaded = await load_candles(client, symbols, timeframes, days, now, pause)
    return {symbol: {tf: frame(candles) for tf, candles in per_tf.items()} for symbol, per_tf in loaded.items()}


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

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


def train_mask(times: np.ndarray, test_start: int, label_seconds: int) -> np.ndarray:
    """Rows whose label was fully known before ``test_start`` (no overlap with the test window)."""
    return times + label_seconds <= test_start


# ---------------------------------------------------------------------------
# Random baseline
# ---------------------------------------------------------------------------

def random_sides(n: int, rate: float, seed, eligible: Optional[np.ndarray] = None) -> np.ndarray:
    """Random entries at ``rate`` of all bars, placed only on ``eligible`` bars (the bars the
    strategy itself could trade, so the random cost profile matches). ``seed`` may be a tuple
    such as (seed, symbol index, period start) so symbols and windows draw independently."""
    rng = np.random.default_rng(seed)
    if eligible is None:
        eligible = np.ones(n, dtype=bool)
    share = eligible.mean() if n else 0.0
    enter = eligible & (rng.random(n) < (min(1.0, rate / share) if share > 0 else 0.0))
    return np.where(enter, rng.choice([1, -1], size=n), 0)


def matched_rate(target_trades: int, rate: float, probe: Callable[[float], int], iterations: int = 2) -> float:
    """Random entry rate whose probe run yields about ``target_trades`` trades.

    Entries while a trade is open are skipped and filters drop some, so the raw
    signal rate undershoots; rescale it against probe runs.
    """
    for _ in range(iterations):
        trades = probe(rate)
        if trades:
            rate = min(1.0, rate * max(target_trades, 1) / trades)
    return rate


# ---------------------------------------------------------------------------
# Trades and checks
# ---------------------------------------------------------------------------

def evaluate_trades(candles: pd.DataFrame, signals: Sequence[Dict[str, Any]], costs: CostModel) -> List[Dict[str, Any]]:
    """Trades of a signal sequence: next-open entries, 1% risk, every signal taken."""
    config = EvaluationConfig(min_confidence_pct=0.0, risk_per_trade=0.01, max_leverage=50.0)
    rows = candles[["time", "open", "high", "low", "close", "volume"]].to_dict("records")
    return evaluate_signal_sequence(rows, list(signals), config, costs)["trades"]


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


def round_trip_cost_r(trades: Sequence[Dict[str, Any]], costs: CostModel) -> float:
    """Mean full round-trip cost in R (fees and slippage on both sides) over these trades."""
    return float(np.mean([costs.round_trip / t["initial_risk_pct"] for t in trades])) if trades else 0.0


def max_drawdown_r(trades: Sequence[Dict[str, Any]]) -> float:
    """Largest peak-to-trough fall of cumulative R, trades taken in exit order."""
    r = np.array([x["r_multiple"] for x in sorted(trades, key=lambda x: x["exit_time"])])
    if not len(r):
        return 0.0
    cum = np.r_[0.0, np.cumsum(r)]
    return float((np.maximum.accumulate(cum) - cum).max())


def positive_windows(windows: List[Dict[str, Any]]) -> int:
    return sum(1 for w in windows if w["model"]["trade_count"] and w["model"]["expectancy_r"] > 0)


def pass_checks(windows: List[Dict[str, Any]], walk_forward: Dict[str, Any], holdout: Dict[str, Any],
                round_trip_cost_r: float, min_holdout_trades: int = 30) -> Dict[str, bool]:
    """The spec's pass criteria. Every walk-forward window counts, traded or not."""
    model, rand = walk_forward["model"], walk_forward["random"]
    return {
        "holdout_expectancy_positive": holdout["model"]["trade_count"] >= min_holdout_trades
            and holdout["model"]["expectancy_r"] > 0,
        "windows_positive_60pct": bool(windows) and positive_windows(windows) / len(windows) >= 0.6,
        "beats_random_by_costs": model["trade_count"] > 0
            and model["expectancy_r"] - rand["expectancy_r"] > round_trip_cost_r,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report_header(width: int, cost: bool = False) -> str:
    return f"{'':<{width}}{'trades':>8}{'win%':>7}{'exp R':>8}{'PF':>6}" + (f"{'cost R':>8}" if cost else "")


def report_line(label: str, s: Dict[str, Any], width: int, cost: bool = False) -> str:
    pf = "-" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"
    line = f"{label:<{width}}{s['trade_count']:>8.0f}{s['win_rate_pct']:>7.1f}{s['expectancy_r']:>8.3f}{pf:>6}"
    return line + (f"{s['avg_cost_r']:>8.3f}" if cost else "")

"""Trade management: the rules for an open position, shared by every engine.

The live auto-trader, the simulated exchange and the backtest evaluator manage
positions through these functions, so a backtest replays the exits the live
trader runs. Callers own only the price source (the mark price live, a bar's
open/high/low in a backtest) and how an action executes (an exchange order, or
a fill at a bar price).

Sides are +1 (long) and -1 (short).
"""

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple

from .config import DEFAULT_LEVERAGE, ROUND_TRIP_COST_PCT
from .trade_plan import TradePlan

BREAKEVEN_MODES = ("legacy", "tp1_fees", "off")
LEGACY_BREAKEVEN_ROE = 0.09  # legacy: a 9% return on margin moves the stop before TP1
LEGACY_BREAKEVEN_OFFSET = 0.0005  # legacy break-even stop: entry + 0.05%
OPPOSING_MIN_CONFIDENCE = 70.0  # an opposite signal at least this confident closes the trade
FALLBACK_BRACKET_ROE = (0.25, 0.40, 0.60)  # stop, TP1, TP2 for a position that arrived without a bracket


@dataclass(frozen=True)
class ExitPolicy:
    """How an open position is managed beyond its bracket."""

    # "legacy": TP1 or a 9% return on margin -> entry + 0.05%; "tp1_fees": TP1 -> entry + costs; "off".
    breakeven: str = "off"
    leverage: float = DEFAULT_LEVERAGE
    # An opposite signal at least this confident closes the trade; None: never.
    opposing_min_confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if self.breakeven not in BREAKEVEN_MODES:
            raise ValueError(f"breakeven must be one of {BREAKEVEN_MODES}")
        if not self.leverage > 0:
            raise ValueError("leverage must be positive")

    @classmethod
    def from_settings(cls, settings: Mapping) -> "ExitPolicy":
        """The auto-trader's exit settings as a policy; the backtest builds the same one."""
        breakeven = settings.get("breakeven_mode", "legacy") if settings.get("auto_breakeven", True) else "off"
        opposing = OPPOSING_MIN_CONFIDENCE if settings.get("opposing_reversal_exit", True) else None
        return cls(breakeven=breakeven, leverage=float(settings.get("leverage", DEFAULT_LEVERAGE)),
                   opposing_min_confidence=opposing)

    @property
    def breakeven_offset(self) -> float:
        """tp1_fees: the stop covers round-trip costs, so a break-even exit does not lose money."""
        return ROUND_TRIP_COST_PCT if self.breakeven == "tp1_fees" else LEGACY_BREAKEVEN_OFFSET

    @property
    def breakeven_trigger_move(self) -> Optional[float]:
        """Favourable price move that moves the stop before TP1 (legacy mode only)."""
        return LEGACY_BREAKEVEN_ROE / self.leverage if self.breakeven == "legacy" else None


def breakeven_stop(policy: ExitPolicy, side: int, entry: float, tp1: float, favourable: float) -> Optional[float]:
    """The break-even stop once a position has earned it, else None.

    ``favourable`` is the best price seen: the mark price live, the bar's high
    (long) or low (short) in a backtest, the TP1 fill price after a partial.
    """
    if policy.breakeven == "off" or entry <= 0:
        return None
    reached_tp1 = tp1 > 0 and side * (favourable - tp1) >= 0
    trigger = policy.breakeven_trigger_move
    moved_enough = trigger is not None and side * (favourable - entry) / entry >= trigger
    if not (reached_tp1 or moved_enough):
        return None
    return entry * (1.0 + side * policy.breakeven_offset)


def breakeven_trigger_price(policy: ExitPolicy, side: int, entry: float, tp1: float) -> Optional[float]:
    """The first price at which ``breakeven_stop`` fires, for display; None when it never does."""
    if policy.breakeven == "off" or entry <= 0:
        return None
    prices = [tp1] if tp1 > 0 else []
    if policy.breakeven_trigger_move is not None:
        prices.append(entry * (1.0 + side * policy.breakeven_trigger_move))
    if not prices:
        return None
    return min(prices) if side == 1 else max(prices)


def trigger_exit(side: int, stop: float, tp1: float, tp2: float, tp1_hit: bool,
                 open_: float, high: float, low: float) -> Optional[Tuple[str, float]]:
    """The bracket leg one price bar fills: ("STOP" | "TP1" | "TP2", fill price), or None.

    The stop wins when a bar touches both a stop and a target; a gap through
    the stop fills at the open. TP2 is live only after TP1 has filled.
    """
    if side == 1:
        if stop > 0 and low <= stop:
            return "STOP", min(stop, open_)
        if not tp1_hit and high >= tp1 > 0:
            return "TP1", tp1
        if tp1_hit and high >= tp2 > 0:
            return "TP2", tp2
    else:
        if stop > 0 and high >= stop:
            return "STOP", max(stop, open_)
        if not tp1_hit and low <= tp1 > 0:
            return "TP1", tp1
        if tp1_hit and low <= tp2 > 0:
            return "TP2", tp2
    return None


def time_stop_due(opened_at: float, max_hold_seconds: Optional[float], now: float) -> bool:
    """True once a position has been held for its plan's maximum holding time."""
    return bool(max_hold_seconds) and now - opened_at >= float(max_hold_seconds)


def opposing_exit(policy: ExitPolicy, side: int, signal: Optional[Mapping],
                  gate: Optional[Callable[[Mapping], bool]] = None) -> bool:
    """True when ``signal`` is an opposite plan strong enough to close a ``side`` position.

    ``gate`` (the edge gate's ``allows``) replaces the confidence threshold when given.
    """
    if policy.opposing_min_confidence is None:
        return False
    plan = TradePlan.from_signal(signal)
    if plan is None or plan.side != -side:
        return False
    if gate is not None:
        return gate(signal)
    return plan.confidence_pct >= policy.opposing_min_confidence


def fallback_bracket(side: int, entry: float, leverage: float) -> Tuple[float, float, float]:
    """Stop, TP1 and TP2 prices for a position that arrived without a bracket."""
    stop_roe, tp1_roe, tp2_roe = FALLBACK_BRACKET_ROE
    lev = max(float(leverage), 1.0)
    return (entry * (1.0 - side * stop_roe / lev),
            entry * (1.0 + side * tp1_roe / lev),
            entry * (1.0 + side * tp2_roe / lev))

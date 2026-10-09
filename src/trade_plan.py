"""Trade plan: the part of a signal that trading acts on.

Every signal producer (the SignalGenerator, research rules) emits the plan's
keys, and every consumer (auto-trader, trade management, backtest evaluator,
edge gate) reads them through ``TradePlan.from_signal``, the one parser.
Display fields riding along in a signal (colours, markers, reason text, ROE
strings) are for the web UI and are never read back.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any, Dict, Mapping, Optional

_SIDES = {"LONG": 1, "SHORT": -1}


def _number(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if isfinite(numeric) else 0.0


@dataclass(frozen=True)
class TradePlan:
    side: int  # +1 long, -1 short
    entry: float  # planned entry price; 0 when the caller fills at the next open
    stop: float
    tp1: float
    tp2: float = 0.0  # 0: TP1 closes the whole position
    confidence_pct: float = 0.0
    max_hold_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.side not in (1, -1):
            raise ValueError("side must be +1 (long) or -1 (short)")

    @property
    def action(self) -> str:
        return "LONG" if self.side == 1 else "SHORT"

    def to_signal(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "entry_price": self.entry,
            "stop_loss": self.stop,
            "take_profit_1": self.tp1,
            "take_profit_2": self.tp2,
            "confidence_pct": self.confidence_pct,
            "max_hold_seconds": self.max_hold_seconds,
        }

    @classmethod
    def from_signal(cls, signal: Optional[Mapping[str, Any]]) -> Optional["TradePlan"]:
        """The plan a signal carries, or None for an empty or neutral signal."""
        if not signal:
            return None
        side = _SIDES.get(str(signal.get("action") or "").upper())
        if side is None:
            return None
        max_hold = _number(signal.get("max_hold_seconds"))
        return cls(
            side=side,
            entry=_number(signal.get("entry_price")),
            stop=_number(signal.get("stop_loss")),
            tp1=_number(signal.get("take_profit_1")),
            tp2=_number(signal.get("take_profit_2")),
            confidence_pct=min(100.0, max(0.0, _number(signal.get("confidence_pct")))),
            max_hold_seconds=max_hold or None,
        )

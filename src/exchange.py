"""The exchange seam: what the auto-trader and the app routes need from a venue.

Two adapters implement it: ``ToobitClient`` (signed HTTP to the live account)
and ``SimulatedExchange`` (the demo account, marked to live prices). Callers
never branch on which one they hold; ``is_demo`` exists only for the
auto-trader's mode-safety check and for display.
"""

import math
from typing import Any, Dict, List, Optional, Protocol


class Exchange(Protocol):
    is_demo: bool
    api_key: str

    async def test_connection(self) -> Dict[str, Any]: ...
    async def get_balance(self) -> Dict[str, Any]: ...
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: ...
    async def get_symbol_rules(self, symbol: str) -> Dict[str, Any]: ...

    async def create_order(self, symbol: str, side: str, order_type: str = "MARKET", quantity: float = 0.0,
                           price: Optional[float] = None, leverage: int = 50, entry_price: float = 0.0,
                           stop_loss: float = 0.0, take_profit_1: float = 0.0,
                           take_profit_2: float = 0.0) -> Dict[str, Any]:
        """Open a position with its bracket (SL + TP1 on half, TP2 on the rest), or close one."""

    async def set_position_tpsl(self, symbol: str, side: str, stop_loss: float = 0.0,
                                take_profit: float = 0.0) -> Dict[str, Any]: ...

    async def flash_close(self, symbol: str) -> Dict[str, Any]:
        """Close at market; the result carries ``realizedPnl`` when the venue reports it."""

    async def cancel_all_orders(self, symbol: str) -> Dict[str, Any]: ...
    async def close(self) -> None: ...


def quantity_to_step(quantity: float, step: float) -> float:
    """Round a quantity down to the contract's step size."""
    step = max(float(step), 1e-8)
    step_text = f"{step:.8f}".rstrip("0")
    decimals = len(step_text.split(".")[1]) if "." in step_text else 0
    return round(math.floor(quantity / step + 1e-9) * step, decimals)  # epsilon: 1.0 // 0.001 == 999

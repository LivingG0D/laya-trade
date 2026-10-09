"""Simulated exchange: the demo account, behind the same interface as the Toobit client.

Positions are marked to a live price source on every query, and their brackets
fill with the trade-management rule the backtest uses (stop first, TP2 only
after TP1), so a demo trade exits the way a backtested one would. Contract
rules come from the live venue when a rules source is given, so demo sizing
matches live sizing.
"""

import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .market_data import symbol_key, to_toobit_symbol
from .trade_management import fallback_bracket, trigger_exit

PriceSource = Callable[[str], Awaitable[float]]
RulesSource = Callable[[str], Awaitable[Dict[str, Any]]]

STARTING_BALANCE = 5000.0
DEFAULT_RULES = {"step_size": 0.001, "tick_size": 0.01, "min_qty": 0.001, "max_qty": 100000.0,
                 "min_notional": 5.0, "multiplier": 1.0}


def _side_sign(position: Dict[str, Any]) -> int:
    return 1 if position["side"] == "LONG" else -1


class SimulatedExchange:
    is_demo = True
    api_key = ""
    is_connected = True

    def __init__(self, price_source: Optional[PriceSource] = None, rules_source: Optional[RulesSource] = None,
                 balance: float = STARTING_BALANCE):
        self.price_source = price_source
        self.rules_source = rules_source
        self.balance = balance
        self.positions: Dict[str, Dict[str, Any]] = {}

    def reset(self) -> None:
        self.positions.clear()
        self.balance = STARTING_BALANCE

    async def _price(self, symbol: str) -> float:
        if self.price_source is None:
            return 0.0
        try:
            return float(await self.price_source(symbol) or 0.0)
        except Exception:
            return 0.0  # keep the last mark while prices are unavailable

    def _realize(self, position: Dict[str, Any], quantity: float, price: float) -> None:
        pnl = _side_sign(position) * (price - position["entryPrice"]) * quantity * position["multiplier"]
        self.balance += pnl
        position["realizedPnl"] = round(position.get("realizedPnl", 0.0) + pnl, 6)
        position["quantity"] -= quantity
        position["margin"] = round(position["quantity"] * position["entryPrice"] * position["multiplier"]
                                   / max(position["leverage"], 1), 6)

    def _mark(self, key: str, price: float) -> None:
        """Fill any bracket leg the price crossed, then mark what is left to ``price``."""
        position = self.positions[key]
        fill = trigger_exit(_side_sign(position), position["stopLoss"], position["takeProfit1"],
                            position["takeProfit2"], position["tp1Filled"], price, price, price)
        if fill is not None:
            leg, fill_price = fill
            if leg == "TP1" and position["takeProfit2"] > 0:
                self._realize(position, position["quantity"] / 2.0, fill_price)
                position["tp1Filled"] = True
            else:
                self._realize(position, position["quantity"], fill_price)
                del self.positions[key]
                return
        position["markPrice"] = price
        pnl = _side_sign(position) * (price - position["entryPrice"]) * position["quantity"] * position["multiplier"]
        position["unrealizedPnl"] = round(pnl, 6)
        position["pnlPercentage"] = round(pnl / position["margin"] * 100, 2) if position["margin"] else 0.0

    async def test_connection(self) -> Dict[str, Any]:
        return {"success": True, "mode": "demo",
                "message": f"Connected to Simulated Demo Account (Virtual ${STARTING_BALANCE:,.0f} USDT Margin)."}

    async def get_balance(self) -> Dict[str, Any]:
        unrealized = sum(p["unrealizedPnl"] for p in self.positions.values())
        used = sum(p["margin"] for p in self.positions.values())
        return {
            "mode": "demo",
            "asset": "USDT",
            "totalBalance": round(self.balance, 2),
            "availableBalance": round(max(0.0, self.balance - used), 2),
            "marginBalance": round(used, 2),
            "unrealizedPnl": round(unrealized, 2),
            "totalEquity": round(self.balance + unrealized, 2),
        }

    async def get_positions(self, symbol: Optional[str] = None,
                            current_market_price: Optional[float] = None) -> List[Dict[str, Any]]:
        """Open positions marked to the latest price; brackets the price crossed have filled."""
        for key in list(self.positions):
            price = current_market_price or await self._price(self.positions[key]["displaySymbol"])
            if price > 0:
                self._mark(key, price)
        wanted = to_toobit_symbol(symbol) if symbol else None
        return [dict(p) for key, p in self.positions.items() if wanted is None or key == wanted]

    async def get_symbol_rules(self, symbol: str) -> Dict[str, Any]:
        if self.rules_source is not None:
            return await self.rules_source(symbol)
        return {"symbol": to_toobit_symbol(symbol), **DEFAULT_RULES}

    def simulate_position(self, symbol: str, side: str, quantity: float, entry_price: float, leverage: int = 50,
                          stop_loss: float = 0.0, take_profit_1: float = 0.0, take_profit_2: float = 0.0,
                          multiplier: float = 1.0) -> Dict[str, Any]:
        """Open or replace a demo position; missing bracket legs get the fallback bracket."""
        key = to_toobit_symbol(symbol)
        side = "SHORT" if str(side).upper() == "SHORT" else "LONG"
        sign = 1 if side == "LONG" else -1
        default_stop, default_tp1, default_tp2 = fallback_bracket(sign, entry_price, leverage)
        position = {
            "symbol": key,
            "rawSymbol": key,
            "displaySymbol": f"{symbol_key(key)}.P",
            "side": side,
            "positionSide": side,
            "quantity": quantity,
            "entryPrice": entry_price,
            "markPrice": entry_price,
            "leverage": leverage,
            "multiplier": multiplier,
            "margin": round(quantity * entry_price * multiplier / max(leverage, 1), 6),
            "unrealizedPnl": 0.0,
            "pnlPercentage": 0.0,
            "realizedPnl": 0.0,
            "liquidationPrice": round(entry_price * (1.0 - sign * 0.9 / max(leverage, 1)), 8),
            "stopLoss": stop_loss if stop_loss > 0 else default_stop,
            "takeProfit1": take_profit_1 if take_profit_1 > 0 else default_tp1,
            "takeProfit2": take_profit_2 if take_profit_2 > 0 else default_tp2,
            "tp1Filled": False,
            "isolated": True,
            "createdAt": int(time.time() * 1000),
        }
        position["takeProfit"] = position["takeProfit1"]
        self.positions[key] = position
        return dict(position)

    async def create_order(self, symbol: str, side: str, order_type: str = "MARKET", quantity: float = 0.0,
                           price: Optional[float] = None, leverage: int = 50, entry_price: float = 0.0,
                           stop_loss: float = 0.0, take_profit_1: float = 0.0,
                           take_profit_2: float = 0.0) -> Dict[str, Any]:
        key = to_toobit_symbol(symbol)
        side_norm = str(side).upper()
        if "CLOSE" in side_norm:
            return await self.flash_close(symbol)  # ponytail: partial closes are not simulated
        position_side = "LONG" if side_norm in ("LONG", "BUY", "BUY_OPEN") else "SHORT"
        fill = entry_price or price or await self._price(symbol)
        if fill <= 0 or quantity <= 0:
            return {"success": False, "mode": "demo", "symbol": key,
                    "message": "A positive quantity and a known price are required."}
        rules = await self.get_symbol_rules(symbol)
        position = self.simulate_position(symbol, position_side, quantity, fill, leverage, stop_loss,
                                          take_profit_1, take_profit_2, float(rules.get("multiplier", 1.0)))
        return {"success": True, "mode": "demo", "orderId": f"demo_{time.time_ns()}", "symbol": key,
                "side": "BUY" if position_side == "LONG" else "SELL", "quantity": quantity, "price": fill,
                "position": position}

    async def set_position_tpsl(self, symbol: str, side: str, stop_loss: float = 0.0, take_profit: float = 0.0,
                                stop_loss_size: Optional[float] = None,
                                take_profit_size: Optional[float] = None) -> Dict[str, Any]:
        key = to_toobit_symbol(symbol)
        position = self.positions.get(key)
        if position is None:
            return {"success": False, "mode": "demo", "symbol": key,
                    "message": "No open demo position found for protection update."}
        if stop_loss <= 0 and take_profit <= 0:
            return {"success": False, "mode": "demo", "symbol": key,
                    "message": "At least one positive stop-loss or take-profit is required."}
        if stop_loss > 0:
            position["stopLoss"] = stop_loss
        if take_profit > 0:
            position["takeProfit1"] = position["takeProfit"] = take_profit
        return {"success": True, "mode": "demo", "symbol": key, "side": position["side"],
                "stopLoss": position["stopLoss"], "takeProfit": position["takeProfit1"], "verified": True}

    async def flash_close(self, symbol: str) -> Dict[str, Any]:
        key = to_toobit_symbol(symbol)
        position = self.positions.get(key)
        if position is None:
            return {"success": False, "mode": "demo", "symbol": key, "message": f"No open demo position found for {key}"}
        price = await self._price(position["displaySymbol"]) or position["markPrice"]
        self._realize(position, position["quantity"], price)
        del self.positions[key]
        pnl = position["realizedPnl"]  # the whole trade, partial fills included
        return {"success": True, "mode": "demo", "symbol": key, "closedPosition": position, "realizedPnl": pnl,
                "message": f"Closed simulated position {key} (PnL: {'+' if pnl >= 0 else '-'}${abs(pnl):.2f})"}

    async def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        position = self.positions.get(to_toobit_symbol(symbol))
        if position is not None:
            position.update(stopLoss=0.0, takeProfit=0.0, takeProfit1=0.0, takeProfit2=0.0)
        return {"success": True, "mode": "demo", "symbol": to_toobit_symbol(symbol), "message": "All orders cancelled"}

    async def close(self) -> None:
        return None

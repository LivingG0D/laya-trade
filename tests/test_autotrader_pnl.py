"""Regression tests for AutoTrader realized-PnL accounting and circuit breakers.

The original defect: `_handle_trade_closed` read `unrealizedPnl` from a position
the exchange had already dropped, fell back to `0.0`, and then booked that zero
as a WIN. Losses were never counted, so the daily-loss circuit breaker could not
fire and the reported win rate was meaningless.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.auto_trader as auto_trader_module
from src.auto_trader import AutoTrader


class FakeMarketService:
    current_symbol = "BTCUSDT.P"
    current_interval = "5m"

    async def fetch_klines(self, symbol, interval, limit=150):
        step = {"5m": 300, "15m": 900, "1h": 3600}.get(interval, 300)
        now = int(time.time())
        candles = []
        for index in range(40):
            price = 100.0 + index * 0.1
            candles.append({
                "time": now - (39 - index) * step,
                "open": price, "high": price + 1.0, "low": price - 1.0,
                "close": price + 0.5, "volume": 1000.0 + index,
            })
        return candles


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")
    client = SimpleNamespace(
        is_demo=True,
        api_key="",
        get_positions=AsyncMock(return_value=[]),
        set_position_tpsl=AsyncMock(return_value={"success": True}),
        get_balance=AsyncMock(return_value={"availableBalance": 100.0, "totalEquity": 100.0}),
        get_symbol_rules=AsyncMock(return_value={"multiplier": 1.0, "step_size": 0.001, "min_qty": 0.001, "min_notional": 5.0}),
        create_order=AsyncMock(return_value={"success": True}),
        flash_close=AsyncMock(return_value={"success": True}),
    )
    trader = AutoTrader(
        exchange=client,
        market_service=FakeMarketService(),
        laya_engine=SimpleNamespace(decide=AsyncMock(return_value={})),
        signal_generator=SimpleNamespace(generate_signal=lambda *a, **k: {}),
    )
    trader.settings["mode"] = "demo"
    return trader, client


def test_realized_pnl_prefers_realized_field():
    value, source = AutoTrader._resolve_realized_pnl({"realizedPnl": -1.25, "unrealizedPnl": 9.0})
    assert value == -1.25
    assert source == "realizedPnl"


def test_realized_pnl_falls_back_to_mark_snapshot():
    value, source = AutoTrader._resolve_realized_pnl({"last_unrealized_pnl": -0.42})
    assert value == -0.42
    assert source == "last_unrealized_pnl"


def test_realized_pnl_returns_none_when_unknown():
    value, source = AutoTrader._resolve_realized_pnl({"symbol": "BTCUSDT.P"})
    assert value is None
    assert source == "unavailable"


def test_loss_is_booked_as_loss_not_win(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader._handle_trade_closed(
        {"symbol": "BTCUSDT.P", "last_unrealized_pnl": -0.75, "last_pnl_pct": -22.0},
        reason="Stop-Loss hit",
    )
    stats = trader.stats
    assert stats["losing_trades"] == 1
    assert stats["winning_trades"] == 0
    assert stats["consecutive_losses"] == 1
    assert stats["daily_realized_pnl"] == -0.75
    assert stats["closed_trades"] == 1


def test_win_resets_consecutive_losses(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader._handle_trade_closed({"symbol": "BTCUSDT.P", "last_unrealized_pnl": -0.5})
    trader._handle_trade_closed({"symbol": "BTCUSDT.P", "last_unrealized_pnl": 1.2})
    assert trader.stats["consecutive_losses"] == 0
    assert trader.stats["winning_trades"] == 1
    assert trader.stats["losing_trades"] == 1


def test_unknown_pnl_is_never_counted_as_a_win(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader._handle_trade_closed({"symbol": "BTCUSDT.P"}, reason="position vanished")
    stats = trader.stats
    assert stats["winning_trades"] == 0
    assert stats["losing_trades"] == 0
    assert stats["unclassified_trades"] == 1
    # A zero/unknown result must not move the daily P&L toward a false win.
    assert stats["daily_realized_pnl"] == 0.0


def test_win_rate_uses_decided_trades_only(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader.stats["total_trades"] = 10          # entries opened
    trader.stats["winning_trades"] = 3
    trader.stats["losing_trades"] = 1
    status = trader.get_status()
    # 3 wins / 4 decided = 75%, not 3/10 entries = 30%.
    assert status["stats"]["win_rate"] == 75.0


def test_daily_loss_breaker_fires_on_realized_losses(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader.settings["max_daily_loss_usd"] = 1.0
    trader.enabled = True
    for _ in range(3):
        trader._handle_trade_closed({"symbol": "BTCUSDT.P", "last_unrealized_pnl": -0.5})

    async def scenario():
        return await trader.tick()

    asyncio.run(scenario())
    assert trader.stats["daily_realized_pnl"] <= -1.0
    assert trader.enabled is False
    assert trader.status == "HALTED"


def test_consecutive_loss_breaker_fires(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    trader.settings["max_consecutive_losses"] = 2
    trader.enabled = True
    for _ in range(2):
        trader._handle_trade_closed({"symbol": "BTCUSDT.P", "last_unrealized_pnl": -0.3})

    async def scenario():
        return await trader.tick()

    asyncio.run(scenario())
    assert trader.enabled is False
    assert "Consecutive" in trader.status_message or "HALTED" in trader.status


def test_vanished_exchange_position_is_booked(tmp_path, monkeypatch):
    """A bracket SL/TP fill removes the position; the result must still be booked."""
    trader, client = make_trader(tmp_path, monkeypatch)
    trader.enabled = True
    trader._set_active_trade({
        "symbol": "BTCUSDT.P",
        "exchange_symbol": "BTCUSDT.P",
        "side": "LONG",
        "last_unrealized_pnl": -0.33,
        "last_pnl_pct": -10.0,
    })
    # The exchange reports no positions -> the position was closed.
    client.get_positions = AsyncMock(return_value=[])

    async def scenario():
        return await trader.tick()

    asyncio.run(scenario())
    assert trader.stats["losing_trades"] == 1
    assert abs(trader.stats["daily_realized_pnl"] + 0.33) < 1e-9
    assert trader.get_active_trade("BTCUSDT.P") is None

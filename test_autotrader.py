"""
Automated Comprehensive Test Suite for AutoTrader & Risk Safeguards.
Tests state transitions, circuit breakers, break-even locking, reversal protection,
and emergency kill switches.
"""

import asyncio
import time
import pytest
from src.toobit_client import ToobitClient
from src.auto_trader import AutoTrader


class MockMarketService:
    def __init__(self):
        self.current_symbol = "BTCUSDT.P"
        self.current_interval = "5m"

    async def fetch_klines(self, symbol, interval, limit=100):
        # Generate 100 mock candles with slight upward trend
        base = 84000.0
        klines = []
        for i in range(limit):
            t = 1700000000 + i * 300
            p = base + i * 5
            klines.append({
                "time": t,
                "open": p - 2,
                "high": p + 8,
                "low": p - 4,
                "close": p,
                "volume": 120.0,
                "is_closed": True
            })
        return klines


class MockLayaEngine:
    def __init__(self):
        self.mock_action = "STRONG_LONG"
        self.mock_conf = 80.0

    async def decide(self, latest):
        return {
            "primary_action": self.mock_action,
            "confidence": self.mock_conf,
            "probabilities": {
                "strong_long": 0.65 if "LONG" in self.mock_action else 0.05,
                "scalp_long": 0.20 if "LONG" in self.mock_action else 0.05,
                "neutral": 0.05,
                "scalp_short": 0.05 if "LONG" in self.mock_action else 0.20,
                "strong_short": 0.05 if "LONG" in self.mock_action else 0.65
            }
        }


class MockSignalGenerator:
    def __init__(self):
        self.action = "STRONG_LONG"
        self.conf = 80.0

    def generate_signal(self, latest, laya_decision):
        p = float(latest.get("close", 84000.0))
        return {
            "action": self.action,
            "confidence_pct": self.conf,
            "entry_price": p,
            "stop_loss": p * 0.995,
            "take_profit_1": p * 1.008,
            "take_profit_2": p * 1.015,
            "targets": {
                "entry": p,
                "stop_loss": p * 0.995,
                "take_profit_1": p * 1.008,
                "take_profit_2": p * 1.015,
            }
        }


async def run_tests():
    print("=" * 60)
    print("RUNNING AUTOTRADER VERIFICATION TEST SUITE")
    print("=" * 60)

    # 1. Initialize ToobitClient in Demo Mode
    client = ToobitClient(is_demo=True)
    # Clear default PEPE position for clean test
    client._demo_positions.clear()
    client._demo_balance = 1000.0

    mkt = MockMarketService()
    laya = MockLayaEngine()
    sig_gen = MockSignalGenerator()

    trader = AutoTrader(
        toobit_client=client,
        market_service=mkt,
        laya_engine=laya,
        signal_generator=sig_gen
    )

    # Test 1: Initial State Check
    assert not trader.enabled, "Trader should be disabled initially"
    assert trader.status == "IDLE", "Status should be IDLE initially"
    print("✅ Test 1: Initial safety state verified (Disabled & IDLE)")

    # Test 2: Enable Auto-Trading
    res = await trader.toggle(True)
    assert res["success"] is True
    assert trader.enabled is True
    assert trader.status == "SCANNING"
    print("✅ Test 2: Toggle ON armed successfully (State: SCANNING)")

    # Test 3: Market Scan & Auto-Order Execution
    # Run tick: should open LONG trade with 50x leverage
    await trader.tick()
    positions = await client.get_positions()
    assert len(positions) == 1, "Should have opened 1 position"
    pos = positions[0]
    assert pos["side"] == "LONG"
    assert pos["leverage"] == 50
    assert trader.status == "IN_POSITION"
    print(f"✅ Test 3: Auto-Order Executed: {pos['side']} {pos['quantity']} {pos['symbol']} @ ${pos['entryPrice']:.2f} (50x)")

    # Test 4: Free Margin Shield
    # Required margin should never exceed 20% of account
    bal = await client.get_balance()
    assert pos["margin"] <= 1000.0 * 0.20, "Margin must be shielded below 20% of equity"
    print(f"✅ Test 4: 80% Free Margin Shield verified: Used ${pos['margin']:.2f} of ${bal['totalEquity']:.2f}")

    # Test 5: Break-Even Trigger
    # Simulate price move to TP1 (+0.8% price move -> +40% ROE)
    mark_p = pos["entryPrice"] * 1.008
    await client.get_positions(current_market_price=mark_p)
    # Run tick to let AutoTrader supervise
    await trader.tick()
    assert trader._active_trade["be_moved"] is True, "Stop loss should be moved to Break-Even at TP1"
    print("✅ Test 5: Break-Even Profit Lock verified (Moved SL to Entry after TP1 reached)")

    # Test 6: Reversal Protection (Flash Close on opposing strong signal)
    # Switch signal to STRONG_SHORT (75% confidence)
    sig_gen.action = "STRONG_SHORT"
    sig_gen.conf = 78.0
    laya.mock_action = "STRONG_SHORT"
    laya.mock_conf = 78.0
    await trader.tick()

    open_pos = await client.get_positions()
    assert len(open_pos) == 0, "Position should be Flash Closed on opposing high-conviction signal"
    assert trader.stats["winning_trades"] >= 1, "Should record as winning trade (closed in profit)"
    assert trader.status == "COOLDOWN", "Should enter cooldown after closing trade"
    print("✅ Test 6: Reversal Protection verified (Closed LONG on strong bearish shift, entered COOLDOWN)")

    # Test 7: Circuit Breaker: Daily Loss Limit
    trader.stats["daily_realized_pnl"] = -6.0  # limit is $5.00
    await trader.tick()
    assert trader.enabled is False, "Trader should halt when daily loss limit breached"
    assert trader.status == "HALTED", "Trader status should be HALTED"
    print("✅ Test 7: Daily Loss Circuit Breaker verified (Halted immediately at -$6.00 loss)")

    # Test 8: Emergency Kill Switch
    # Re-enable trader and add a demo position
    trader.stats["daily_realized_pnl"] = 0.0
    await trader.toggle(True)
    client.simulate_position("BTCUSDT.P", "LONG", 0.01, 84000.0, 50)
    assert len(await client.get_positions()) == 1

    kill_res = await trader.emergency_stop()
    assert kill_res["success"] is True
    assert trader.enabled is False
    assert len(await client.get_positions()) == 0
    print("✅ Test 8: Emergency Kill Switch verified (Disabled trader & wiped all open positions)")

    print("=" * 60)
    print("ALL 8 AUTOTRADER SAFETY TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 60)

def test_demo_mode_halts_when_client_is_live(monkeypatch, tmp_path):
    """A trader must never route a demo configuration to a live exchange client."""
    import src.auto_trader as auto_trader_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")

    client = SimpleNamespace(
        is_demo=False,
        api_key="live-key",
        get_positions=AsyncMock(return_value=[]),
        get_balance=AsyncMock(return_value={"availableBalance": 1000.0, "totalEquity": 1000.0}),
        get_symbol_rules=AsyncMock(return_value={
            "multiplier": 1.0,
            "step_size": 0.001,
            "min_qty": 0.001,
            "min_notional": 5.0,
        }),
        create_order=AsyncMock(return_value={"success": True}),
    )
    trader = AutoTrader(
        toobit_client=client,
        market_service=MockMarketService(),
        laya_engine=MockLayaEngine(),
        signal_generator=MockSignalGenerator(),
    )
    trader.enabled = True
    trader.status = "SCANNING"

    asyncio.run(trader.tick())

    assert client.create_order.await_count == 0
    assert trader.enabled is False
    assert trader.status == "HALTED"


def test_emergency_stop_reports_failed_close(monkeypatch, tmp_path):
    """Emergency stop must not claim closure when the exchange rejects a close."""
    import src.auto_trader as auto_trader_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")

    client = SimpleNamespace(
        cancel_all_orders=AsyncMock(return_value={"success": True}),
        get_positions=AsyncMock(return_value=[{
            "rawSymbol": "BTC-SWAP-USDT",
            "symbol": "BTC-SWAP-USDT",
            "side": "LONG",
        }]),
        flash_close=AsyncMock(return_value={"success": False, "message": "close rejected"}),
    )
    trader = AutoTrader(
        toobit_client=client,
        market_service=SimpleNamespace(current_symbol="BTCUSDT.P"),
        laya_engine=None,
        signal_generator=None,
    )

    result = asyncio.run(trader.emergency_stop())

    assert trader.enabled is False
    assert result["success"] is False
    assert result["closed_count"] == 0


def test_failed_exchange_bracket_is_retried_instead_of_marked_protected(monkeypatch, tmp_path):
    """A failed TP/SL update must not populate the local protected-trade state."""
    import src.auto_trader as auto_trader_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")

    client = SimpleNamespace(
        is_demo=True,
        api_key="",
        get_positions=AsyncMock(return_value=[{
            "rawSymbol": "BTC-SWAP-USDT",
            "displaySymbol": "BTCUSDT.P",
            "side": "LONG",
            "quantity": 1.0,
            "entryPrice": 100.0,
            "markPrice": 100.0,
            "unrealizedPnl": 0.0,
            "pnlPercentage": 0.0,
            "margin": 10.0,
            "stopLoss": 0.0,
            "takeProfit1": 0.0,
        }]),
        set_position_tpsl=AsyncMock(return_value={"success": False, "message": "exchange rejected bracket"}),
    )
    trader = AutoTrader(
        toobit_client=client,
        market_service=MockMarketService(),
        laya_engine=MockLayaEngine(),
        signal_generator=MockSignalGenerator(),
    )
    trader.enabled = True
    trader.settings["auto_breakeven"] = False
    trader.settings["opposing_reversal_exit"] = False

    asyncio.run(trader.tick())

    assert client.set_position_tpsl.await_count == 1
    assert trader._active_trade["stop_loss"] == 0.0
    assert trader._active_trade["take_profit_1"] == 0.0


if __name__ == "__main__":
    asyncio.run(run_tests())

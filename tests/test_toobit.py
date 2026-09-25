import asyncio
import sys
import os
import time
import pytest

# Add root directory to pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding='utf-8')

from src.toobit_client import ToobitClient, generate_position_suggestion
from src.market_data import to_toobit_symbol


async def run_tests():
    print("=== TEST 1: ToobitClient Initialization & Demo Mode ===")
    client = ToobitClient(is_demo=True)
    assert client.is_connected, "Client should be marked connected in demo mode"
    balance = await client.get_balance()
    print("Demo Balance:", balance)
    assert balance["availableBalance"] > 0, "Available balance should be > 0"

    print("\n=== TEST 2: Demo Positions Querying & Real-Time Mark Calculation ===")
    positions = await client.get_positions(current_market_price=0.004300)
    print(f"Active positions count: {len(positions)}")
    assert len(positions) == 1, "Default demo position should be present"
    pos = positions[0]
    print("Position details:", pos)
    assert pos["side"] == "LONG"
    assert pos["unrealizedPnl"] > 0, "At 0.004300 with 0.00418 entry, Long should be in profit"

    print("\n=== TEST 3: AI Position Suggestion Generation ===")
    mock_laya = {
        "primary_action": "STRONG_LONG",
        "confidence": 85,
        "probabilities": {
            "strong_long": 0.60,
            "scalp_long": 0.25,
            "neutral": 0.10,
            "scalp_short": 0.04,
            "strong_short": 0.01
        }
    }
    mock_signal = {
        "action": "LONG",
        "confidence": 85,
        "targets": {
            "entry": 0.00418,
            "stop_loss": 0.00412,
            "take_profit_1": 0.00445,
            "take_profit_2": 0.00460
        }
    }
    # Test A: Holding LONG + Bullish market
    sug_aligned = generate_position_suggestion(pos, 0.004300, mock_signal, mock_laya)
    print("Suggestion (Aligned):", sug_aligned["badgeText"], "|", sug_aligned["headline"])
    assert sug_aligned["alignment"] == "ALIGNED"
    assert "HOLD" in sug_aligned["action"]

    # Test B: Holding LONG + Bearish market reversal
    mock_laya_bear = {
        "primary_action": "STRONG_SHORT",
        "confidence": 82,
        "probabilities": {
            "strong_long": 0.02,
            "scalp_long": 0.08,
            "neutral": 0.10,
            "scalp_short": 0.35,
            "strong_short": 0.45
        }
    }
    mock_signal_bear = {
        "action": "SHORT",
        "confidence": 82,
        "targets": {
            "entry": 0.00430,
            "stop_loss": 0.00438,
            "take_profit_1": 0.00410
        }
    }
    sug_opposed = generate_position_suggestion(pos, 0.004300, mock_signal_bear, mock_laya_bear)
    print("Suggestion (Opposed / In Profit):", sug_opposed["badgeText"], "|", sug_opposed["headline"])
    assert sug_opposed["alignment"] == "OPPOSED"
    assert sug_opposed["action"] == "TAKE_PROFIT"
    assert sug_opposed["canFlashClose"] is True

    # Test C: No position
    sug_none = generate_position_suggestion(None, 0.004300, mock_signal, mock_laya)
    print("Suggestion (No Position):", sug_none["badgeText"], "|", sug_none["headline"])
    assert sug_none["hasPosition"] is False
    assert sug_none["action"] == "ENTER_SETUP"

    print("\n=== TEST 4: Flash Close Execution ===")
    close_res = await client.flash_close(pos["displaySymbol"])
    print("Flash Close Result:", close_res)
    assert close_res["success"] is True

    remaining = await client.get_positions()
    print("Positions after close:", len(remaining))
    assert len(remaining) == 0, "Position should be closed"

    print("\n=== TEST 5: Demo Simulation (Opening Short) ===")
    sim_pos = client.simulate_position("1000PEPEUSDT.P", "SHORT", 200000, 0.00435, leverage=10)
    print("Simulated Short:", sim_pos)
    assert sim_pos["side"] == "SHORT"
    assert sim_pos["quantity"] == 200000

    print("\n=== TEST 6: HMAC Signature Generation Verification ===")
    auth_client = ToobitClient("my_api_key", "my_api_secret", is_demo=False)
    test_params = {"symbol": "1000PEPE-SWAP-USDT", "timestamp": 1700000000000}
    sig = auth_client._generate_signature(test_params)
    print(f"Generated HMAC SHA-256 signature ({len(sig)} chars): {sig[:16]}...")
    assert len(sig) == 64, "HMAC-SHA256 signature should be 64 hex characters"

    # Regression guard: Toobit signs the exact query string that is sent, in the
    # order it is sent (never sorted). Verified against the documented example at
    # https://api-docs.toobit.com/api/spot-example.html
    doc_secret = "30lfjDT51iOG1kYZnDoLNynOyMdIcmQyO1XYfxzYOmQfx9tjiI98Pzio4uhZ0Uk2"
    doc_params = {
        "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": 1, "price": 400, "recvWindow": 100000, "timestamp": 1668481902307,
    }
    doc_client = ToobitClient("k", doc_secret, is_demo=False)
    doc_sig = doc_client._generate_signature(doc_params)
    assert doc_sig == "8420e499e71cce4a00946db16543198b6bcae01791bdb75a06b5a7098b156468", (
        "Signature must match Toobit's documented vector - params are signed in "
        f"sent order, not sorted. Got {doc_sig}"
    )
    print("Signature matches Toobit documented vector (order-preserving):", doc_sig[:16], "...")
    await doc_client.close()

    await client.close()
    await auth_client.close()
    print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")


def test_live_position_query_failure_is_not_flat(monkeypatch):
    """A live position query failure must remain unknown, never look flat."""
    client = ToobitClient("test-key", "test-secret", is_demo=False)

    async def fail_request(*_args, **_kwargs):
        raise RuntimeError("position service unavailable")

    monkeypatch.setattr(client, "_send_signed_request", fail_request)

    async def scenario():
        with pytest.raises(RuntimeError, match="position service unavailable"):
            await client.get_positions()
        await client.close()

    asyncio.run(scenario())


def test_cancel_all_orders_reports_failure_when_exchange_rejects(monkeypatch):
    """A failed cancel-all operation must never be reported as successful."""
    client = ToobitClient("test-key", "test-secret", is_demo=False)

    async def fail_request(*_args, **_kwargs):
        raise RuntimeError("cancel endpoint unavailable")

    monkeypatch.setattr(client, "_send_signed_request", fail_request)

    async def scenario():
        result = await client.cancel_all_orders("BTCUSDT.P")
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert "cancel" in result["message"].lower()


def test_live_entry_stops_when_margin_setup_fails(monkeypatch):
    """A live entry must not dispatch after an unconfirmed margin setup."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret", is_demo=False)
    client.set_margin_type = AsyncMock(return_value={"success": False, "message": "margin rejected"})
    client.set_leverage = AsyncMock(return_value={"success": True})
    client._send_signed_request = AsyncMock(return_value={"orderId": "should-not-submit"})

    async def scenario():
        result = await client.create_order(
            symbol="BTCUSDT.P",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            leverage=50,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit_1=102.0,
            take_profit_2=104.0,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert client._send_signed_request.await_count == 0


def test_live_entry_fails_when_protection_cannot_be_confirmed(monkeypatch):
    """A filled entry with failed protection must be reported as unsafe."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret", is_demo=False)
    client.set_margin_type = AsyncMock(return_value={"success": True})
    client.set_leverage = AsyncMock(return_value={"success": True})
    client._send_signed_request = AsyncMock(return_value={"orderId": "entry-order"})
    client.create_plan_order = AsyncMock(return_value={"success": False, "message": "plan rejected"})
    client.flash_close = AsyncMock(return_value={"success": True, "message": "closed"})

    async def scenario():
        result = await client.create_order(
            symbol="BTCUSDT.P",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            leverage=50,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit_1=102.0,
            take_profit_2=104.0,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert client.flash_close.await_count == 1


def test_live_entry_places_position_bracket_and_separate_tp2(monkeypatch):
    """A confirmed entry must use position-level SL/TP1 plus a separate TP2."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret", is_demo=False)
    client.set_margin_type = AsyncMock(return_value={"success": True})
    client.set_leverage = AsyncMock(return_value={"success": True})
    client._send_signed_request = AsyncMock(return_value={"orderId": "entry-order"})
    client.set_position_tpsl = AsyncMock(return_value={"success": True, "verified": True})
    client.create_plan_order = AsyncMock(return_value={"success": True, "orderId": "tp2-order"})

    async def scenario():
        result = await client.create_order(
            symbol="BTCUSDT.P",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            leverage=50,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit_1=102.0,
            take_profit_2=104.0,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    client.set_position_tpsl.assert_awaited_once_with(
        symbol="BTC-SWAP-USDT",
        side="LONG",
        stop_loss=99.0,
        take_profit=102.0,
        stop_loss_size=0.01,
        take_profit_size=0.005,
    )
    client.create_plan_order.assert_awaited_once_with(
        symbol="BTC-SWAP-USDT",
        side="SELL_CLOSE",
        order_type="TAKE_PROFIT_MARKET",
        quantity=0.005,
        trigger_price=104.0,
        reduce_only=True,
        position_side="LONG",
        take_profit_slot="takeProfit2",
    )


@pytest.mark.parametrize(
    ("position_side", "stop_loss", "take_profit", "expected_exit_side"),
    [
        ("LONG", 90.0, 110.0, "SELL_CLOSE"),
        ("SHORT", 110.0, 90.0, "BUY_CLOSE"),
    ],
)
def test_protection_payload_uses_position_side_and_separate_tp2(
    position_side,
    stop_loss,
    take_profit,
    expected_exit_side,
):
    """LONG/SHORT protection must never be sent to the opposite position side."""
    client = ToobitClient("test-key", "test-secret", is_demo=False)
    client._contract_rules_cache["BTC-SWAP-USDT"] = {"tick_size": 0.01}
    calls = []

    async def fake_send(method, endpoint, params=None, **_kwargs):
        params = dict(params or {})
        calls.append((endpoint, params))
        if endpoint.endswith("/position/trading-stop"):
            return {
                "code": 0,
                "side": params["side"],
                "stopLoss": params.get("stopLoss", ""),
                "takeProfit": params.get("takeProfit", ""),
            }
        return {"code": 0, "orderId": "tp2-order"}

    client._send_signed_request = fake_send

    async def scenario():
        result = await client._attach_entry_protection(
            symbol="BTC-SWAP-USDT",
            position_side=position_side,
            quantity=10,
            stop_loss=stop_loss,
            take_profit_1=take_profit,
            take_profit_2=take_profit + (5 if position_side == "LONG" else -5),
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert len(calls) == 2
    assert calls[0][0].endswith("/position/trading-stop")
    assert calls[0][1]["side"] == position_side
    assert calls[1][0].endswith("/futures/order")
    assert calls[1][1]["side"] == expected_exit_side
    assert calls[1][1]["type"] == "STOP"


def test_demo_positions_receive_default_protection():
    client = ToobitClient(is_demo=True)
    client._demo_positions.clear()

    position = client.simulate_position(
        "BTCUSDT.P",
        "LONG",
        quantity=1,
        entry_price=100.0,
        leverage=10,
    )

    assert position["stopLoss"] > 0
    assert position["takeProfit1"] > 0
    assert position["takeProfit2"] > 0


def test_low_price_protection_is_not_rounded_to_zero():
    client = ToobitClient("test-key", "test-secret", is_demo=False)
    client._contract_rules_cache["ETH-SWAP-USDT"] = {"tick_size": 0.01}
    client._last_rules_cache_time = time.time()
    captured = {}

    async def fake_send(method, endpoint, params=None, **_kwargs):
        captured.update(params or {})
        return {
            "code": 0,
            "side": "LONG",
            "stopLoss": params["stopLoss"],
            "takeProfit": params["takeProfit"],
        }

    client._send_signed_request = fake_send

    async def scenario():
        result = await client.set_position_tpsl(
            "ETHUSDT.P",
            "LONG",
            stop_loss=0.004,
            take_profit=0.005,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert float(captured["stopLoss"]) > 0
    assert float(captured["takeProfit"]) > 0


def test_position_advice_accepts_current_uppercase_decision_schema():
    """Current Laya output must not fall back to legacy default probabilities."""
    result = generate_position_suggestion(
        position=None,
        current_price=100.0,
        signal={},
        laya_decision={
            "position": "STRONG_LONG",
            "confidence_prob": 0.81,
            "probabilities": {
                "STRONG_LONG": 0.70,
                "SCALP_LONG": 0.10,
                "SCALP_SHORT": 0.05,
                "STRONG_SHORT": 0.05,
                "NEUTRAL_HOLD": 0.10,
            },
        },
    )

    assert result["action"] == "ENTER_SETUP"
    assert "81.0% confidence" in result["suggestion"]

    active_result = generate_position_suggestion(
        position={
            "side": "LONG",
            "entryPrice": 100.0,
            "unrealizedPnl": 0.0,
            "pnlPercentage": 0.0,
            "quantity": 1.0,
            "displaySymbol": "BTCUSDT.P",
        },
        current_price=100.0,
        signal={},
        laya_decision={
            "position": "STRONG_LONG",
            "confidence_prob": 0.81,
            "probabilities": {
                "STRONG_LONG": 0.70,
                "SCALP_LONG": 0.10,
                "SCALP_SHORT": 0.05,
                "STRONG_SHORT": 0.05,
                "NEUTRAL_HOLD": 0.10,
            },
        },
    )
    assert "80% bullish" in active_result["suggestion"]


if __name__ == "__main__":
    asyncio.run(run_tests())

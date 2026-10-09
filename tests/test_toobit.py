import asyncio
import sys
import os
import time
import pytest

# Add root directory to pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.position_advice import generate_position_suggestion
from src.toobit_client import ToobitClient


def test_signature_signs_the_sent_query_string_in_order():
    """Toobit signs the exact query string that is sent, in the order it is sent (never sorted).
    Verified against the documented example at https://api-docs.toobit.com/api/spot-example.html"""
    client = ToobitClient("my_api_key", "my_api_secret")
    assert len(client._generate_signature({"symbol": "1000PEPE-SWAP-USDT", "timestamp": 1700000000000})) == 64

    doc_secret = "30lfjDT51iOG1kYZnDoLNynOyMdIcmQyO1XYfxzYOmQfx9tjiI98Pzio4uhZ0Uk2"
    doc_params = {
        "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": 1, "price": 400, "recvWindow": 100000, "timestamp": 1668481902307,
    }
    doc_client = ToobitClient("k", doc_secret)
    assert doc_client._generate_signature(doc_params) == (
        "8420e499e71cce4a00946db16543198b6bcae01791bdb75a06b5a7098b156468"
    )
    asyncio.run(client.close())
    asyncio.run(doc_client.close())


def test_live_position_query_failure_is_not_flat(monkeypatch):
    """A live position query failure must remain unknown, never look flat."""
    client = ToobitClient("test-key", "test-secret")

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
    client = ToobitClient("test-key", "test-secret")

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

    client = ToobitClient("test-key", "test-secret")
    client.get_symbol_leverage_config = AsyncMock(return_value={"success": True, "marginType": "CROSS"})
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


def test_live_entry_preserves_margin_setup_error(monkeypatch):
    """A failed margin setup must expose the exchange rejection to the caller."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret")
    client.get_symbol_leverage_config = AsyncMock(return_value={"success": True, "marginType": "CROSS"})
    client.set_margin_type = AsyncMock(return_value={
        "success": False,
        "error": "Toobit API Error (400): [-4048] Margin type cannot be changed if there exists position.",
    })
    client.set_leverage = AsyncMock(return_value={"success": True})
    client._send_signed_request = AsyncMock(return_value={"orderId": "should-not-submit"})

    async def scenario():
        result = await client.create_order(
            symbol="SOLUSDT.P",
            side="BUY",
            order_type="MARKET",
            quantity=1.82,
            leverage=50,
            entry_price=120.0,
            stop_loss=119.0,
            take_profit_1=122.0,
            take_profit_2=124.0,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert "[-4048]" in result["message"]
    assert "position" in result["message"].lower()
    assert "cross-margined" in result["message"]
    assert client._send_signed_request.await_count == 0


def test_live_entry_skips_margin_change_when_already_isolated():
    """An already-isolated symbol must enter without re-issuing a blocked margin change."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret")
    client.get_symbol_leverage_config = AsyncMock(return_value={"success": True, "marginType": "ISOLATED"})
    client.set_margin_type = AsyncMock()
    client.set_leverage = AsyncMock(return_value={"success": True})
    client._attach_entry_protection = AsyncMock(return_value={"success": True, "bracket": [], "errors": []})
    client._send_signed_request = AsyncMock(return_value={"orderId": "entry-order"})

    async def scenario():
        result = await client.create_order(
            symbol="SOLUSDT.P",
            side="BUY",
            order_type="MARKET",
            quantity=1.82,
            leverage=50,
            entry_price=120.0,
            stop_loss=119.0,
            take_profit_1=122.0,
            take_profit_2=124.0,
        )
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert client.set_margin_type.await_count == 0
    assert client._send_signed_request.await_args.args[1] == "/api/v1/futures/order"


def test_symbol_leverage_config_reads_current_margin_mode():
    """The account config read must expose the symbol margin mode used to gate the write."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret")
    client._send_signed_request = AsyncMock(return_value=[
        {"symbolId": "SOL-SWAP-USDT", "leverage": "10", "marginType": "ISOLATED"}
    ])

    async def scenario():
        result = await client.get_symbol_leverage_config("SOLUSDT.P")
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["marginType"] == "ISOLATED"
    assert result["leverage"] == "10"
    assert client._send_signed_request.await_args.args[:2] == ("GET", "/api/v1/futures/accountLeverage")
    assert client._send_signed_request.await_args.args[2] == {"symbol": "SOL-SWAP-USDT"}


def test_live_entry_fails_when_protection_cannot_be_confirmed(monkeypatch):
    """A filled entry with failed protection must be reported as unsafe."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret")
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

    client = ToobitClient("test-key", "test-secret")
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
    client = ToobitClient("test-key", "test-secret")
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


def test_low_price_protection_is_not_rounded_to_zero():
    client = ToobitClient("test-key", "test-secret")
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


def test_flash_close_market_fallback_reports_success():
    """When flashClose fails, the reduce-only market close must not demand entry protection."""
    from unittest.mock import AsyncMock

    client = ToobitClient("test-key", "test-secret")
    client._last_positions = [{"symbol": "BTC-SWAP-USDT", "side": "LONG"}]
    client.get_positions = AsyncMock(return_value=[{"symbol": "BTC-SWAP-USDT", "side": "LONG", "quantity": 0.01}])
    calls = []

    async def fake_send(method, endpoint, params=None, **_kwargs):
        calls.append((endpoint, dict(params or {})))
        if endpoint.endswith("/flashClose"):
            raise RuntimeError("flash close unavailable")
        return {"orderId": "close-order"}

    client._send_signed_request = fake_send

    async def scenario():
        result = await client.flash_close("BTCUSDT.P")
        await client.close()
        return result

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert calls[-1][0] == "/api/v1/futures/order"
    assert calls[-1][1]["side"] == "SELL_CLOSE"
    assert not any(endpoint.endswith("trading-stop") for endpoint, _ in calls)


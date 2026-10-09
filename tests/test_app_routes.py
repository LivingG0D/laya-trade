"""App routes against the simulated exchange: no network, state written to a temp file."""

import pytest
from starlette.testclient import TestClient

import src.app as app_module
import src.auto_trader as auto_trader_module
from src.config import SUPPORTED_SYMBOLS
from src.simulated_exchange import SimulatedExchange
from src.toobit_client import ToobitClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")
    exchange = SimulatedExchange()
    trader = app_module.auto_trader
    monkeypatch.setattr(app_module, "exchange", exchange)
    monkeypatch.setattr(trader, "exchange", exchange)
    monkeypatch.setattr(trader, "settings", dict(trader.settings, mode="demo"))
    monkeypatch.setattr(trader, "stats", dict(trader.stats))
    monkeypatch.setattr(trader, "_active_trades", {})
    monkeypatch.setattr(trader, "enabled", False)
    return TestClient(app_module.app)  # no lifespan: nothing streams or loads a model


def test_autotrade_controls(client):
    status = client.get("/api/autotrade/status").json()
    assert {"enabled", "settings", "stats", "logs"} <= set(status)

    assert client.post("/api/autotrade/toggle", json={"enabled": True}).json()["enabled"] is True
    settings = client.post("/api/autotrade/settings", json={"settings": {"fixed_margin_usd": 2.0}}).json()["settings"]
    assert settings["fixed_margin_usd"] == 2.0
    assert client.post("/api/autotrade/emergency_stop").json()["success"] is True
    assert client.post("/api/autotrade/clear_logs").status_code == 200
    assert client.get("/api/autotrade/status").json()["enabled"] is False


def test_manual_close_books_the_managed_trade(client):
    exchange = app_module.exchange
    exchange.simulate_position("BTCUSDT.P", "LONG", quantity=1.0, entry_price=100.0, leverage=10)
    app_module.auto_trader._set_active_trade({"symbol": "BTCUSDT.P", "exchange_symbol": "BTC-SWAP-USDT",
                                              "side": "LONG", "open_time": 0})
    exchange.positions["BTC-SWAP-USDT"]["markPrice"] = 99.0

    assert client.post("/api/toobit/close", json={"symbol": "BTCUSDT.P"}).json()["success"] is True
    stats = app_module.auto_trader.stats
    assert stats["losing_trades"] == 1 and stats["daily_realized_pnl"] == pytest.approx(-1.0)


def test_chart_switch_validates_and_never_widens_the_trading_allow_list(client, monkeypatch):
    async def no_stream(symbol, interval):
        return None

    monkeypatch.setattr(app_module.market_service, "start_streaming", no_stream)
    before = list(SUPPORTED_SYMBOLS)
    assert client.post("/api/switch", params={"symbol": "ORDIUSDT.P", "interval": "5m"}).status_code == 200
    assert client.post("/api/switch", params={"symbol": "../etc", "interval": "5m"}).status_code == 400
    assert client.post("/api/switch", params={"symbol": "BTCUSDT.P", "interval": "7m"}).status_code == 400
    assert SUPPORTED_SYMBOLS == before


def test_simulating_never_flips_a_live_account_to_demo(client, monkeypatch):
    live = ToobitClient("key", "secret")
    monkeypatch.setattr(app_module, "exchange", live)
    response = client.post("/api/toobit/demo/simulate", json={"symbol": "BTCUSDT.P", "entryPrice": 100.0})
    assert response.status_code == 409 and app_module.exchange is live

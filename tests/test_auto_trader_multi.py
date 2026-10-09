import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.auto_trader as auto_trader_module
from src.auto_trader import AutoTrader


class FakeMarketService:
    current_symbol = "BTCUSDT.P"
    current_interval = "5m"

    def __init__(self, stale=False):
        self.stale = stale
        self.fetch_calls = []

    async def fetch_klines(self, symbol, interval, limit=150):
        self.fetch_calls.append((symbol, interval))
        step = {"5m": 300, "15m": 900, "1h": 3600}.get(interval, 300)
        now = int(time.time())
        if self.stale:
            now -= 100000
        candles = []
        for index in range(40):
            price = 100.0 + index * 0.1
            candles.append({
                "time": now - (39 - index) * step,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 1000.0 + index,
            })
        return candles


class FakeLayaEngine:
    async def decide(self, market_state):
        return {
            "position": "STRONG_LONG",
            "long_prob": 0.72,
            "short_prob": 0.18,
            "hold_prob": 0.10,
            "directional_conviction": 0.78,
            "confidence_score": 3,
            "engine": "test",
        }


class FakeSignalGenerator:
    def generate_signal(self, market_state, decision):
        price = float(market_state.get("price", 100.0))
        return {
            "symbol": market_state.get("symbol"),
            "action": "LONG",
            "confidence_pct": 80.0,
            "entry_price": price,
            "stop_loss": price - 2.0,
            "take_profit_1": price + 3.0,
            "take_profit_2": price + 5.0,
            "targets": {
                "stop_loss": price - 2.0,
                "take_profit_1": price + 3.0,
                "take_profit_2": price + 5.0,
            },
        }


def make_trader(tmp_path, monkeypatch, positions=None, symbols=None, shadow=True):
    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")
    client = SimpleNamespace(
        is_demo=True,
        api_key="",
        get_positions=AsyncMock(return_value=positions or []),
        set_position_tpsl=AsyncMock(return_value={
            "success": True,
            "stopLoss": 98.0,
            "takeProfit": 103.0,
        }),
        get_balance=AsyncMock(return_value={
            "availableBalance": 100.0,
            "totalEquity": 100.0,
        }),
        get_symbol_rules=AsyncMock(return_value={
            "multiplier": 1.0,
            "step_size": 0.001,
            "min_qty": 0.001,
            "min_notional": 5.0,
        }),
        create_order=AsyncMock(return_value={"success": True}),
        flash_close=AsyncMock(return_value={"success": True}),
    )
    trader = AutoTrader(
        exchange=client,
        market_service=FakeMarketService(),
        laya_engine=FakeLayaEngine(),
        signal_generator=FakeSignalGenerator(),
    )
    trader.settings["mode"] = "demo"
    trader.settings["symbols"] = symbols or ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"]
    trader.settings["timeframes"] = ["5m", "15m", "1h"]
    trader.settings["max_positions"] = 2
    trader.settings["shadow_mode"] = shadow
    return trader, client


def test_settings_support_two_symbols_and_timeframes(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)

    async def scenario():
        return await trader.update_settings({
            "symbols": ["BTCUSDT.P", "ETHUSDT.P", "BTCUSDT.P", "not-supported"],
            "timeframes": ["5m", "1h", "4h", "bad"],
            "max_positions": 9,
        })

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert trader.settings["max_positions"] == 2
    assert trader.settings["symbols"] == ["BTCUSDT.P", "ETHUSDT.P"]
    assert trader.settings["timeframes"] == ["5m", "1h", "4h"]


def test_parallel_scanner_returns_candidates_for_each_symbol(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch, symbols=["BTCUSDT.P", "ETHUSDT.P"])

    async def scenario():
        return await trader._scan_candidates([])

    candidates = asyncio.run(scenario())

    assert {candidate["symbol"] for candidate in candidates} == {"BTCUSDT.P", "ETHUSDT.P"}
    assert all(candidate["signal"]["action"] == "LONG" for candidate in candidates)
    assert set(trader._analysis_results) == {"BTCUSDT.P", "ETHUSDT.P"}


def test_two_open_positions_are_managed_without_opening_a_third(tmp_path, monkeypatch):
    positions = [
        {
            "rawSymbol": "BTC-SWAP-USDT",
            "displaySymbol": "BTCUSDT.P",
            "side": "LONG",
            "entryPrice": 100.0,
            "markPrice": 100.0,
            "quantity": 1.0,
            "margin": 10.0,
            "pnlPercentage": 0.0,
            "stopLoss": 98.0,
            "takeProfit1": 103.0,
            "takeProfit2": 105.0,
        },
        {
            "rawSymbol": "ETH-SWAP-USDT",
            "displaySymbol": "ETHUSDT.P",
            "side": "LONG",
            "entryPrice": 100.0,
            "markPrice": 100.0,
            "quantity": 1.0,
            "margin": 10.0,
            "pnlPercentage": 0.0,
            "stopLoss": 98.0,
            "takeProfit1": 103.0,
            "takeProfit2": 105.0,
        },
    ]
    trader, client = make_trader(tmp_path, monkeypatch, positions=positions, shadow=False)
    trader.enabled = True

    asyncio.run(trader.tick())

    assert len(trader.get_active_trades()) == 2
    client.create_order.assert_not_called()
    assert {trade["exchange_symbol"] for trade in trader.get_active_trades()} == {
        "BTC-SWAP-USDT",
        "ETH-SWAP-USDT",
    }


def test_one_open_position_allows_one_more_candidate(tmp_path, monkeypatch):
    positions = [{
        "rawSymbol": "BTC-SWAP-USDT",
        "displaySymbol": "BTCUSDT.P",
        "side": "LONG",
        "entryPrice": 100.0,
        "markPrice": 100.0,
        "quantity": 1.0,
        "margin": 10.0,
        "pnlPercentage": 0.0,
        "stopLoss": 98.0,
        "takeProfit1": 103.0,
        "takeProfit2": 105.0,
    }]
    trader, client = make_trader(
        tmp_path,
        monkeypatch,
        positions=positions,
        symbols=["ETHUSDT.P", "SOLUSDT.P"],
        shadow=False,
    )
    trader.enabled = True

    asyncio.run(trader.tick())

    client.create_order.assert_awaited_once()
    assert client.create_order.await_args.kwargs["symbol"] in {"ETHUSDT.P", "SOLUSDT.P"}
    assert len(trader.get_active_trades()) == 2


def test_stale_timeframe_data_is_rejected(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch, symbols=["BTCUSDT.P"])
    trader.market_service = FakeMarketService(stale=True)

    async def scenario():
        return await trader._analyze_symbol("BTCUSDT.P")

    assert asyncio.run(scenario()) is None
    assert trader._analysis_results["BTCUSDT.P"]["state"] == "ERROR"

import asyncio

import pytest

from src.market_data import MarketDataService


def candle(time, close):
    return {
        "time": time,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


def test_kline_cache_separates_requested_limits(monkeypatch):
    service = MarketDataService()
    calls = []

    async def fake_fetch(symbol, interval, limit):
        calls.append(limit)
        return [candle(index, 100 + index) for index in range(limit)]

    monkeypatch.setattr(service, "_fetch_toobit_klines", fake_fetch)

    async def scenario():
        await service.fetch_klines("BTCUSDT.P", "5m", 100)
        await service.fetch_klines("BTCUSDT.P", "5m", 350)
        await service.close()

    asyncio.run(scenario())

    assert calls == [100, 350]
    assert "BTCUSDT.P_5m_100" in service._klines_cache
    assert "BTCUSDT.P_5m_350" in service._klines_cache


def test_htf_context_does_not_label_short_history_as_ema200(monkeypatch):
    service = MarketDataService()

    async def fake_klines(symbol, interval, limit):
        return [candle(index, 100 + index) for index in range(100)]

    monkeypatch.setattr(service, "fetch_klines", fake_klines)

    async def scenario():
        result = await service.fetch_htf_context("BTCUSDT.P")
        await service.close()
        return result

    result = asyncio.run(scenario())

    assert result["ema200"] is None
    assert result["htf_ema200"] is None
    assert result["trend"] in {"NEUTRAL", "BULLISH", "BEARISH"}


def test_htf_context_rejects_invalid_candles(monkeypatch):
    service = MarketDataService()

    async def fake_klines(symbol, interval, limit):
        return [candle(1, 100), candle(1, 101)]

    monkeypatch.setattr(service, "fetch_klines", fake_klines)

    async def scenario():
        result = await service.fetch_htf_context("BTCUSDT.P")
        await service.close()
        return result

    result = asyncio.run(scenario())

    assert result["trend"] == "NEUTRAL"
    assert result["ema200"] is None

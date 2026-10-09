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


def test_rest_klines_mark_the_forming_bar_open():
    from src.market_data import _parse_klines

    rows = [[t * 1000, 1, 2, 0.5, 1.5, 10] for t in (0, 300, 600)]
    closed = [c["is_closed"] for c in _parse_klines(rows, "5m", now=900)]
    assert closed == [True, True, True]
    assert [c["is_closed"] for c in _parse_klines(rows, "5m", now=899)] == [True, True, False]

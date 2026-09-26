import asyncio

import httpx

from experiments.history import fetch_range, load_history

STEP = 300
T0 = 1_700_000_000 - (1_700_000_000 % STEP)
BARS = 4000


def fake_binance(requests):
    """Binance-shaped klines for BARS 5m candles starting at T0, honouring endTime/limit."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        requests.append(params)
        end_ms = int(params["endTime"])
        limit = int(params["limit"])
        rows = []
        for i in range(BARS):
            open_ms = (T0 + i * STEP) * 1000
            if open_ms > end_ms:
                break
            price = 100 + i * 0.01
            rows.append([open_ms, price, price + 1, price - 1, price, 10, open_ms + STEP * 1000 - 1, 0, 5, 4])
        return httpx.Response(200, json=rows[-limit:])

    return handler


def client_for(requests):
    return httpx.AsyncClient(transport=httpx.MockTransport(fake_binance(requests)))


def test_fetch_range_pages_back_without_gaps_or_duplicates():
    requests = []
    start = T0 + 100 * STEP
    now = T0 + BARS * STEP  # every candle closed

    async def run():
        async with client_for(requests) as client:
            return await fetch_range(client, "BTCUSDT.P", "5m", start, now, page=1500)

    candles = asyncio.run(run())

    times = [c["time"] for c in candles]
    assert times[0] == start
    assert times[-1] == T0 + (BARS - 1) * STEP
    assert all(b - a == STEP for a, b in zip(times, times[1:]))
    assert len(requests) == 3
    assert requests[0]["symbol"] == "BTCUSDT"


def test_fetch_range_drops_unclosed_last_candle():
    requests = []
    now = T0 + (BARS - 1) * STEP + 100  # last candle still forming

    async def run():
        async with client_for(requests) as client:
            return await fetch_range(client, "BTCUSDT.P", "5m", T0 + 3900 * STEP, now)

    candles = asyncio.run(run())

    assert candles[-1]["time"] == T0 + (BARS - 2) * STEP


def test_load_history_reuses_cache_and_fetches_only_the_tail(tmp_path):
    requests = []
    days = 5  # 1440 bars

    async def run(now):
        async with client_for(requests) as client:
            return await load_history(client, "BTCUSDT.P", "5m", days, tmp_path, now=now)

    first_now = T0 + 3000 * STEP
    first = asyncio.run(run(first_now))
    first_calls = len(requests)

    later_now = T0 + 3100 * STEP
    second = asyncio.run(run(later_now))

    assert len(requests) - first_calls == 1  # tail only
    assert second[-1]["time"] == T0 + 3099 * STEP
    assert second[0]["time"] >= later_now - days * 86400
    assert first[-1]["time"] == T0 + 2999 * STEP
    times = [c["time"] for c in second]
    assert all(b - a == STEP for a, b in zip(times, times[1:]))


def test_load_history_falls_back_to_cache_when_network_fails(tmp_path):
    requests = []

    async def run(now, transport):
        async with httpx.AsyncClient(transport=transport) as client:
            return await load_history(client, "BTCUSDT.P", "5m", 5, tmp_path, now=now)

    first = asyncio.run(run(T0 + 3000 * STEP, httpx.MockTransport(fake_binance(requests))))

    def down(request):
        raise httpx.ConnectError("offline")

    cached = asyncio.run(run(T0 + 3100 * STEP, httpx.MockTransport(down)))
    assert cached[-1]["time"] == first[-1]["time"]


def test_fetch_range_retries_rate_limits(tmp_path):
    requests = []
    good = fake_binance(requests)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"code": -1003})
        return good(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as client:
            return await fetch_range(client, "BTCUSDT.P", "5m", T0 + 3900 * STEP, T0 + BARS * STEP,
                                     retry_delay=0)

    candles = asyncio.run(run())
    assert calls["n"] == 2
    assert candles[-1]["time"] == T0 + (BARS - 1) * STEP

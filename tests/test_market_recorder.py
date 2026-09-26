import asyncio
import json

import httpx
import pytest

from experiments.market_recorder import (
    book_features,
    fetch_series,
    load_rows,
    merge_series,
    snapshot,
    write_rows,
)

Q = 900_000  # 15 minutes in ms


def test_book_features_match_jev_style_state():
    bids = [["100.0", "5"], ["99.95", "10"], ["99.8", "20"], ["98.0", "100"]]
    asks = [["100.1", "4"], ["100.2", "6"], ["100.5", "30"], ["102.5", "100"]]
    f = book_features(bids, asks)
    assert f["mid"] == pytest.approx(100.05)
    assert f["spread_bps"] == pytest.approx(0.1 / 100.05 * 1e4)
    # within 10 bps of mid (99.95..100.15): bids 5+10, asks 4
    assert f["depth_bps"]["10"] == {"bid": 15.0, "ask": 4.0}
    # within 1% (99.05..101.05): bids 35, asks 40 -> imbalance (35-40)/75
    assert f["imbalance_1pct"] == pytest.approx(-5 / 75)
    assert f["top5"]["bids"][0] == "100.0 x 5"


def test_merge_series_joins_by_time_and_funding_asof():
    series = {
        "oi": [{"timestamp": Q, "sumOpenInterest": "10", "sumOpenInterestValue": "1000"},
               {"timestamp": 2 * Q, "sumOpenInterest": "11", "sumOpenInterestValue": "1100"}],
        "taker": [{"timestamp": 2 * Q, "buyVol": "3", "sellVol": "1", "buySellRatio": "3"}],
    }
    funding = [{"fundingTime": Q - 5, "fundingRate": "0.0001"}]
    rows = merge_series("BTCUSDT.P", series, funding)
    assert [r["time"] for r in rows] == [Q // 1000, 2 * Q // 1000]
    assert rows[0]["oi"] == 10.0 and "taker_buy_vol" not in rows[0]
    assert rows[1]["taker_buy_vol"] == 3.0 and rows[1]["taker_sell_vol"] == 1.0
    assert rows[1]["funding_rate"] == pytest.approx(0.0001)


def test_fetch_series_pages_back_without_duplicates():
    points = [{"timestamp": i * Q, "sumOpenInterest": str(i)} for i in range(1, 1201)]

    def handler(request):
        # Binance ignores startTime here: it returns the newest `limit` points up to endTime.
        p = dict(request.url.params)
        end, limit = int(p["endTime"]), int(p["limit"])
        rows = [x for x in points if x["timestamp"] <= end][-limit:]
        return httpx.Response(200, json=rows)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_series(client, "/futures/data/openInterestHist", "BTCUSDT", Q, 1200 * Q, pause=0)

    rows = asyncio.run(run())
    assert [r["timestamp"] for r in rows] == [i * Q for i in range(1, 1201)]


def test_snapshot_builds_one_live_row():
    def handler(request):
        path = request.url.path
        if path.endswith("depth"):
            return httpx.Response(200, json={"bids": [["100", "1"]], "asks": [["100.2", "2"]]})
        if path.endswith("premiumIndex"):
            return httpx.Response(200, json={"markPrice": "100.1", "indexPrice": "100.0", "lastFundingRate": "0.0002"})
        if path.endswith("openInterestHist"):
            return httpx.Response(200, json=[{"timestamp": 2 * Q, "sumOpenInterest": "7", "sumOpenInterestValue": "700"}])
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": 2 * Q, "buyVol": "1", "sellVol": "2", "buySellRatio": "0.5"}])
        return httpx.Response(200, json=[{"timestamp": 2 * Q, "longShortRatio": "1.5", "longAccount": "0.6"}])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await snapshot(client, "BTCUSDT.P", now_s=2 * Q // 1000 + 10)

    row = asyncio.run(run())
    assert row["time"] == 2 * Q // 1000 and row["source"] == "live"
    assert row["oi"] == 7.0 and row["funding_rate"] == pytest.approx(0.0002)
    assert row["basis_bps"] == pytest.approx(10.0)
    assert row["book"]["mid"] == pytest.approx(100.1)


def test_rows_dedupe_by_time_live_overrides_backfill(tmp_path):
    path = tmp_path / "BTCUSDT_P.jsonl"
    write_rows(path, [{"time": 1, "oi": 1.0, "source": "backfill"}, {"time": 2, "oi": 2.0, "source": "backfill"}])
    write_rows(path, [{"time": 2, "book": {"mid": 5}, "source": "live"}])
    rows = load_rows(path)
    assert [r["time"] for r in rows] == [1, 2]
    assert rows[1]["oi"] == 2.0 and rows[1]["book"] == {"mid": 5} and rows[1]["source"] == "live"
    assert len(path.read_text().splitlines()) == 3  # append-only file, merged on read
    json.loads(path.read_text().splitlines()[-1])

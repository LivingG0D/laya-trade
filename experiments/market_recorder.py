"""Record 15m derivatives and order-book data that candles do not contain.

Per symbol and 15-minute bar: open interest, account and top-trader long/short
ratios, taker buy/sell volume, funding rate, mark/index basis, and (live only)
an order-book snapshot shaped like jev-trader's state: mid, spread, imbalance
within 1% of mid, depth within 10/25/50 bps, top 5 levels per side.

Binance keeps only ~30 days of the 15m ratio/OI series, so `--backfill`
fetches that month now; `--loop` then appends a live row after every 15m close.
Rows go to data/market_log/{SYMBOL}.jsonl (append-only, merged by time on read).
Liquidations are not recorded: Binance's REST endpoint for them needs an API key.

Run:  python experiments/market_recorder.py --backfill --loop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import BASE_DIR, BINANCE_FUTURES_REST_URL  # noqa: E402
from src.market_data import to_binance_symbol  # noqa: E402

BAR_S = 900
LOG_DIR = BASE_DIR / "data" / "market_log"
SYMBOLS = "BTCUSDT.P,ETHUSDT.P,SOLUSDT.P,BNBUSDT.P,XRPUSDT.P,DOGEUSDT.P,SUIUSDT.P,AVAXUSDT.P,LINKUSDT.P,LTCUSDT.P"

# series name -> (endpoint, {row field: response field})
SERIES = {
    "oi": ("/futures/data/openInterestHist", {"oi": "sumOpenInterest", "oi_value": "sumOpenInterestValue"}),
    "global_ls": ("/futures/data/globalLongShortAccountRatio",
                  {"global_ls_ratio": "longShortRatio", "global_long_pct": "longAccount"}),
    "top_ls": ("/futures/data/topLongShortPositionRatio",
               {"top_pos_ls_ratio": "longShortRatio", "top_long_pct": "longAccount"}),
    "taker": ("/futures/data/takerlongshortRatio",
              {"taker_buy_vol": "buyVol", "taker_sell_vol": "sellVol", "taker_buy_sell_ratio": "buySellRatio"}),
}
DEPTH_BANDS_BPS = (10, 25, 50)


def book_features(bids: List[List[str]], asks: List[List[str]]) -> Dict[str, Any]:
    """Mid, spread, imbalance within 1% of mid, cumulative depth per band, top 5 levels."""
    b = [(float(p), float(q)) for p, q in bids]
    a = [(float(p), float(q)) for p, q in asks]
    mid = (b[0][0] + a[0][0]) / 2

    def within(frac):
        bid = sum(q for p, q in b if p >= mid * (1 - frac))
        ask = sum(q for p, q in a if p <= mid * (1 + frac))
        return bid, ask

    bid1, ask1 = within(0.01)
    return {
        "mid": mid,
        "spread_bps": (a[0][0] - b[0][0]) / mid * 1e4,
        "imbalance_1pct": (bid1 - ask1) / (bid1 + ask1) if bid1 + ask1 else 0.0,
        "depth_bps": {str(bps): dict(zip(("bid", "ask"), within(bps / 1e4))) for bps in DEPTH_BANDS_BPS},
        "top5": {"bids": [f"{p} x {q}" for p, q in bids[:5]], "asks": [f"{p} x {q}" for p, q in asks[:5]]},
    }


def merge_series(symbol: str, series: Dict[str, List[dict]], funding: List[dict]) -> List[Dict[str, Any]]:
    """One row per timestamp across the series; funding rate as of that time."""
    rows: Dict[int, Dict[str, Any]] = {}
    for name, points in series.items():
        fields = SERIES[name][1]
        for point in points:
            t = int(point["timestamp"]) // 1000
            row = rows.setdefault(t, {"time": t, "symbol": symbol, "source": "backfill"})
            for out, src in fields.items():
                if point.get(src) is not None:
                    row[out] = float(point[src])
    funding = sorted(funding, key=lambda f: int(f["fundingTime"]))
    out = []
    for t in sorted(rows):
        rate = [float(f["fundingRate"]) for f in funding if int(f["fundingTime"]) // 1000 <= t]
        if rate:
            rows[t]["funding_rate"] = rate[-1]
        out.append(rows[t])
    return out


async def _get(client: httpx.AsyncClient, path: str, params: dict, retries: int = 4) -> Any:
    for attempt in range(retries + 1):
        try:
            resp = await client.get(BINANCE_FUTURES_REST_URL + path, params=params)
        except httpx.TransportError:
            if attempt == retries:
                raise
            await asyncio.sleep(2 ** attempt)
            continue
        if resp.status_code in (418, 429) or resp.status_code >= 500:
            if attempt == retries:
                resp.raise_for_status()
            await asyncio.sleep(5 * 2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()


async def fetch_series(client: httpx.AsyncClient, path: str, symbol: str, start_ms: int, end_ms: int,
                       pause: float = 0.3, limit: int = 500) -> List[dict]:
    """All 15m points between start and end. Binance ignores startTime on these endpoints and
    returns the newest ``limit`` points up to endTime, so page backwards by endTime."""
    out: Dict[int, dict] = {}
    cursor = end_ms
    while cursor >= start_ms:
        batch = await _get(client, path, {"symbol": symbol, "period": "15m", "limit": limit, "endTime": cursor})
        if not batch:
            break
        for point in batch:
            if start_ms <= int(point["timestamp"]) <= end_ms:
                out[int(point["timestamp"])] = point
        first = int(batch[0]["timestamp"])
        if len(batch) < limit or first > cursor:
            break
        cursor = first - 1
        if pause:
            await asyncio.sleep(pause)
    return [out[t] for t in sorted(out)]


async def backfill(client: httpx.AsyncClient, symbol: str, days: float = 29.5) -> List[Dict[str, Any]]:
    sym = to_binance_symbol(symbol)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86400 * 1000)
    series = {name: await fetch_series(client, path, sym, start_ms, end_ms) for name, (path, _) in SERIES.items()}
    funding = await _get(client, "/fapi/v1/fundingRate", {"symbol": sym, "startTime": start_ms - 86400_000,
                                                          "endTime": end_ms, "limit": 1000})
    return merge_series(symbol, series, funding)


async def snapshot(client: httpx.AsyncClient, symbol: str, now_s: Optional[float] = None) -> Dict[str, Any]:
    """Live row for the 15m bar that just closed: latest series points, funding, basis and the book."""
    sym = to_binance_symbol(symbol)
    now_s = time.time() if now_s is None else now_s
    row: Dict[str, Any] = {"time": int(now_s) // BAR_S * BAR_S, "symbol": symbol, "source": "live",
                           "recorded_at": round(now_s, 1)}
    for path, fields in SERIES.values():
        points = await _get(client, path, {"symbol": sym, "period": "15m", "limit": 1})
        if points:
            row.update({out: float(points[-1][src]) for out, src in fields.items() if points[-1].get(src) is not None})
    premium = await _get(client, "/fapi/v1/premiumIndex", {"symbol": sym})
    mark, index = float(premium["markPrice"]), float(premium["indexPrice"])
    row.update(mark=mark, index=index, basis_bps=(mark - index) / index * 1e4,
               funding_rate=float(premium["lastFundingRate"]))
    depth = await _get(client, "/fapi/v1/depth", {"symbol": sym, "limit": 100})
    row["book"] = book_features(depth["bids"], depth["asks"])
    return row


def write_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_rows(path: Path) -> List[Dict[str, Any]]:
    """Rows merged by time; later lines (live) add to and override earlier ones (backfill)."""
    merged: Dict[int, Dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                merged.setdefault(row["time"], {}).update(row)
    return [merged[t] for t in sorted(merged)]


def log_path(symbol: str) -> Path:
    return LOG_DIR / f"{symbol.upper().replace('.', '_')}.jsonl"


async def main_async(args) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    async with httpx.AsyncClient(timeout=30.0) as client:
        if args.backfill:
            for symbol in symbols:
                rows = await backfill(client, symbol)
                write_rows(log_path(symbol), rows)
                print(f"backfill {symbol}: {len(rows)} rows", flush=True)
        while args.loop or args.once:
            if args.loop:
                wait = BAR_S - time.time() % BAR_S + 10  # 10 s after each 15m close, once Binance has the point
                await asyncio.sleep(wait)
            for symbol in symbols:
                try:
                    write_rows(log_path(symbol), [await snapshot(client, symbol)])
                except Exception as exc:  # keep recording the other symbols
                    print(f"{time.strftime('%H:%M:%S')} {symbol}: {exc!r}", file=sys.stderr, flush=True)
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} recorded {len(symbols)} symbols", flush=True)
            if args.once:
                break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default=SYMBOLS)
    parser.add_argument("--backfill", action="store_true", help="fetch the ~30 days Binance still has")
    parser.add_argument("--once", action="store_true", help="record one live row per symbol now")
    parser.add_argument("--loop", action="store_true", help="record a live row after every 15m close")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

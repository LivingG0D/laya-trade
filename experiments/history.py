"""Closed-candle history for backtests, paged from Binance futures and cached on disk.

Toobit's klines endpoint ignores ``endTime`` paging, so history comes from
Binance USDT-M futures. Prices track Toobit closely but are not identical.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import BINANCE_FUTURES_REST_URL  # noqa: E402
from src.market_data import _parse_klines, to_binance_symbol  # noqa: E402

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
KLINES_URL = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/klines"


async def fetch_range(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start: int,
    now: Optional[float] = None,
    page: int = 1500,
    pause: float = 0.0,
    retries: int = 4,
    retry_delay: float = 5.0,
) -> List[Dict[str, Any]]:
    """Closed candles with open time >= ``start`` (seconds), oldest first.

    ``pause`` spaces out page requests (Binance weighs a 1500-candle page at 10 of
    2400 per minute); rate limits (429/418) and 5xx responses are retried with backoff.
    """
    now = time.time() if now is None else now
    step = INTERVAL_SECONDS[interval]
    rows: List[list] = []
    end_ms = int(now * 1000)
    while True:
        params = {"symbol": to_binance_symbol(symbol), "interval": interval, "limit": page, "endTime": end_ms}
        for attempt in range(retries + 1):
            try:
                resp = await client.get(KLINES_URL, params=params)
            except httpx.TransportError:
                if attempt == retries:
                    raise
                await asyncio.sleep(retry_delay * 2 ** attempt)
                continue
            if resp.status_code not in (418, 429) and resp.status_code < 500 or attempt == retries:
                break
            await asyncio.sleep(retry_delay * 2 ** attempt)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        earliest = int(batch[0][0])
        if earliest <= start * 1000 or len(batch) < page:
            break
        end_ms = earliest - 1
        if pause:
            await asyncio.sleep(pause)
    return [c for c in _parse_klines(rows) if c["time"] >= start and c["time"] + step <= now]


async def load_history(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    days: float,
    cache_dir: Path,
    now: Optional[float] = None,
    pause: float = 0.0,
) -> List[Dict[str, Any]]:
    """``days`` of closed candles, reusing the disk cache and fetching only what is missing."""
    now = time.time() if now is None else now
    step = INTERVAL_SECONDS[interval]
    start = int(now - days * 86400)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol.upper().replace('.', '_')}_{interval}.json"

    cached: List[Dict[str, Any]] = json.loads(path.read_text()) if path.exists() else []
    if cached and cached[0]["time"] <= start + step:
        try:
            tail = await fetch_range(client, symbol, interval, cached[-1]["time"] + step, now, pause=pause)
        except httpx.HTTPError as exc:
            # Offline or rate limited: the cache still covers the window, minus the newest bars.
            print(f"history: using cache for {symbol} {interval} ({exc!r})", file=sys.stderr)
            tail = []
        by_time = {c["time"]: c for c in cached + tail}
        candles = [by_time[t] for t in sorted(by_time)]
    else:
        candles = await fetch_range(client, symbol, interval, start, now, pause=pause)
    path.write_text(json.dumps(candles))
    return [c for c in candles if c["time"] >= start]

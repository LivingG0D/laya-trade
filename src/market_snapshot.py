"""Market snapshot: the closed-candle state a decision is made on.

The auto-trader, the dashboard analysis and the backtest all build their state
here, so all three decide on the same thing: indicator telemetry of closed
candles only, merged across timeframes. A candle is closed once
``open + interval <= now``; the candle source never decides that, because REST
rows and the WebSocket both carry the still-forming bar.

The candle source is a ``fetch(symbol=, interval=, limit=)`` coroutine:
``MarketDataService.fetch_klines`` live. The backtest replays history through
``closed_window`` cut at each bar's close.
"""

import asyncio
import bisect
import time
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from .indicators import compute_all_indicators

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
WINDOW = 150  # closed candles per timeframe a decision sees
CONTEXT_TIMEFRAMES = ("15m", "1h")  # context the dashboard adds above its chart interval

Fetch = Callable[..., Awaitable[List[Dict[str, Any]]]]


def closed_window(candles: Sequence[Mapping[str, Any]], interval: str, at: float, size: int = WINDOW,
                  opens: Optional[Sequence[int]] = None) -> List[Mapping[str, Any]]:
    """The last ``size`` candles closed at or before ``at`` (pass ``opens`` to reuse open times)."""
    opens = opens if opens is not None else [c["time"] for c in candles]
    closed = bisect.bisect_right(opens, at - INTERVAL_SECONDS[interval])
    return list(candles[max(0, closed - size):closed])


def is_fresh(candle_time: Any, interval: str, now: float) -> bool:
    """A closed candle is fresh until two intervals after it opened (plus clock skew)."""
    try:
        age = now - float(candle_time)
    except (TypeError, ValueError):
        return False
    return float(candle_time) > 0 and -60 <= age <= INTERVAL_SECONDS[interval] * 2 + 30


def timeframe_snapshot(candles: List[Dict[str, Any]], symbol: str, timeframe: str, leverage: int) -> Dict[str, Any]:
    """Latest indicator telemetry for one timeframe of closed candles."""
    calculated = compute_all_indicators(candles)
    latest = dict(calculated["latest"])
    latest["symbol"] = symbol
    latest["interval"] = timeframe
    latest["time"] = calculated.get("series", [{}])[-1].get("time", candles[-1].get("time", 0))
    latest["leverage"] = int(leverage)
    return latest


def higher_timeframe(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """The higher-timeframe context a decision reads, from one timeframe's snapshot."""
    return {
        "trend": snapshot.get("trend_state", "NEUTRAL"),
        "interval": snapshot.get("interval"),
        "rsi": snapshot.get("rsi"),
        "adx": snapshot.get("adx"),
    }


def merge_timeframes(latest_by_timeframe: Dict[str, Dict[str, Any]],
                     entry_timeframe: Optional[str] = None) -> Dict[str, Any]:
    """Entry-timeframe state enriched with higher-timeframe context.

    The entry timeframe defaults to 5m when present, else the last one given;
    the higher-timeframe context is the 1h snapshot, else the 15m one.
    """
    entry = entry_timeframe or ("5m" if "5m" in latest_by_timeframe else list(latest_by_timeframe)[-1])
    state = dict(latest_by_timeframe[entry])
    higher = latest_by_timeframe.get("1h") or latest_by_timeframe.get("15m")
    if higher:
        state["htf"] = higher_timeframe(higher)
    state["multi_timeframe"] = {
        timeframe: {
            "time": snapshot.get("time"),
            "trend": snapshot.get("trend_state"),
            "rsi": snapshot.get("rsi"),
            "adx": snapshot.get("adx"),
        }
        for timeframe, snapshot in latest_by_timeframe.items()
    }
    return state


async def snapshot_timeframe(fetch: Fetch, symbol: str, timeframe: str, leverage: int,
                             now: Optional[float] = None) -> Dict[str, Any]:
    """One timeframe's snapshot from a live candle source; raises when its candles are missing or stale."""
    now = time.time() if now is None else now
    candles = closed_window(await fetch(symbol=symbol, interval=timeframe, limit=WINDOW + 1), timeframe, now)
    if not candles:
        raise RuntimeError(f"No closed {timeframe} candles for {symbol}")
    latest = timeframe_snapshot(candles, symbol, timeframe, leverage)
    if not is_fresh(latest.get("time"), timeframe, now):
        raise RuntimeError(f"Latest closed {timeframe} candle for {symbol} is stale")
    return latest


async def market_state(fetch: Fetch, symbol: str, timeframes: Sequence[str], leverage: int,
                       entry_timeframe: Optional[str] = None,
                       now: Optional[float] = None) -> Dict[str, Any]:
    """The merged decision state across ``timeframes``, fetched concurrently."""
    snapshots = await asyncio.gather(*(snapshot_timeframe(fetch, symbol, tf, leverage, now) for tf in timeframes))
    return merge_timeframes(dict(zip(timeframes, snapshots)), entry_timeframe)


def dashboard_timeframes(interval: str) -> List[str]:
    """The chart interval plus the auto-trader's context timeframes above it (5m -> 5m, 15m, 1h)."""
    return [interval] + [tf for tf in CONTEXT_TIMEFRAMES if INTERVAL_SECONDS[tf] > INTERVAL_SECONDS[interval]]

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional, Callable
import httpx
import websockets

from .config import (
    TOOBIT_REST_URL,
    TOOBIT_WS_URL,
    BINANCE_FUTURES_REST_URL,
    BINANCE_FUTURES_WS_URL,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    DEFAULT_EXCHANGE
)

logger = logging.getLogger("market_data")


def to_toobit_symbol(symbol: str) -> str:
    """
    Convert canonical symbol to Toobit perpetual futures symbol.
    e.g. '1000PEPEUSDT.P' or '1000PEPEUSDT' -> '1000PEPE-SWAP-USDT'
         'BTCUSDT.P' or 'BTCUSDT' -> 'BTC-SWAP-USDT'
    """
    s = symbol.upper().strip()
    if "-SWAP-" in s:
        return s
    if s.endswith(".P"):
        s = s[:-2]
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}-SWAP-USDT"
    return s


def to_binance_symbol(symbol: str) -> str:
    """
    Convert symbol to Binance futures format.
    e.g. '1000PEPEUSDT.P' or '1000PEPE-SWAP-USDT' -> '1000PEPEUSDT'
    """
    s = symbol.upper().strip()
    if s.endswith(".P"):
        s = s[:-2]
    s = s.replace("-SWAP-", "")
    return s


class MarketDataService:
    def __init__(
        self,
        exchange: str = DEFAULT_EXCHANGE,
        rest_url: Optional[str] = None,
        ws_url: Optional[str] = None
    ):
        self.exchange = exchange
        self.rest_url = rest_url or (TOOBIT_REST_URL if exchange == "Toobit" else BINANCE_FUTURES_REST_URL)
        self.ws_url = ws_url or (TOOBIT_WS_URL if exchange == "Toobit" else BINANCE_FUTURES_WS_URL)
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._ws_task: Optional[asyncio.Task] = None
        self._subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        self._running = False
        self.current_symbol = DEFAULT_SYMBOL
        self.current_interval = DEFAULT_TIMEFRAME

    async def close(self):
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        await self._http_client.aclose()

    async def fetch_klines(
        self,
        symbol: str = DEFAULT_SYMBOL,
        interval: str = DEFAULT_TIMEFRAME,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical klines from Toobit (primary) with Binance fallback.
        Returns parsed list sorted from oldest to newest.
        """
        if self.exchange == "Toobit":
            try:
                return await self._fetch_toobit_klines(symbol, interval, limit)
            except Exception as e:
                logger.warning(f"Toobit kline fetch failed ({e}), falling back to Binance Futures...")
                return await self._fetch_binance_klines(symbol, interval, limit)
        else:
            return await self._fetch_binance_klines(symbol, interval, limit)

    async def _fetch_toobit_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        toobit_sym = to_toobit_symbol(symbol)
        url = f"{TOOBIT_REST_URL}/quote/v1/klines"
        params = {
            "symbol": toobit_sym,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        resp = await self._http_client.get(url, params=params)
        resp.raise_for_status()
        raw_klines = resp.json()

        klines = []
        for item in raw_klines:
            # Toobit format:
            # [0: Open time ms, 1: Open, 2: High, 3: Low, 4: Close, 5: Volume, 6: Close time, 7: Quote volume, 8: Count, 9: Taker buy base, 10: Taker buy quote]
            open_time_sec = int(item[0] // 1000)
            klines.append({
                "time": open_time_sec,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "taker_buy_volume": float(item[9]) if len(item) > 9 else 0.0,
                "trades": int(item[8]) if len(item) > 8 else 0,
                "is_closed": True
            })
        klines.sort(key=lambda k: k["time"])
        return klines

    async def _fetch_binance_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        binance_sym = to_binance_symbol(symbol)
        url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/klines"
        params = {
            "symbol": binance_sym,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        resp = await self._http_client.get(url, params=params)
        resp.raise_for_status()
        raw_klines = resp.json()

        klines = []
        for item in raw_klines:
            open_time_sec = int(item[0] // 1000)
            klines.append({
                "time": open_time_sec,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "taker_buy_volume": float(item[9]),
                "trades": int(item[8]),
                "is_closed": True
            })
        klines.sort(key=lambda k: k["time"])
        return klines

    async def fetch_ticker_24h(self, symbol: str = DEFAULT_SYMBOL) -> Dict[str, Any]:
        """Fetch 24h price stats from Toobit with Binance fallback."""
        if self.exchange == "Toobit":
            try:
                toobit_sym = to_toobit_symbol(symbol)
                url = f"{TOOBIT_REST_URL}/quote/v1/ticker/24hr"
                params = {"symbol": toobit_sym}
                resp = await self._http_client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    d = data[0]
                else:
                    d = data
                return {
                    "symbol": symbol,
                    "exchange": "Toobit",
                    "last_price": float(d.get("c", 0)),
                    "price_change": float(d.get("pc", 0)),
                    "price_change_percent": float(d.get("pcp", 0)) * 100.0,
                    "high_price": float(d.get("h", 0)),
                    "low_price": float(d.get("l", 0)),
                    "volume": float(d.get("v", 0)),
                    "quote_volume": float(d.get("qv", 0)),
                }
            except Exception as e:
                logger.warning(f"Toobit 24h ticker failed ({e}), falling back to Binance...")

        # Binance fallback
        try:
            binance_sym = to_binance_symbol(symbol)
            url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/ticker/24hr"
            params = {"symbol": binance_sym}
            resp = await self._http_client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "symbol": symbol,
                "exchange": "Binance",
                "last_price": float(data.get("lastPrice", 0)),
                "price_change": float(data.get("priceChange", 0)),
                "price_change_percent": float(data.get("priceChangePercent", 0)),
                "high_price": float(data.get("highPrice", 0)),
                "low_price": float(data.get("lowPrice", 0)),
                "volume": float(data.get("volume", 0)),
                "quote_volume": float(data.get("quoteVolume", 0)),
            }
        except Exception as e:
            logger.error(f"Error fetching 24h ticker for {symbol}: {e}")
            return {}

    def subscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        """Subscribe to real-time candle tick updates."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify_subscribers(self, candle_update: Dict[str, Any]):
        for sub in list(self._subscribers):
            try:
                res = sub(candle_update)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error notifying subscriber: {e}")

    async def start_streaming(self, symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_TIMEFRAME):
        """Start or restart the background WebSocket stream for symbol and interval."""
        self.current_symbol = symbol.upper()
        self.current_interval = interval
        self._running = True

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self.exchange == "Toobit":
            self._ws_task = asyncio.create_task(self._toobit_stream_loop())
        else:
            self._ws_task = asyncio.create_task(self._binance_stream_loop())

    async def _toobit_stream_loop(self):
        toobit_sym = to_toobit_symbol(self.current_symbol)
        url = TOOBIT_WS_URL
        logger.info(f"Connecting to Toobit WebSocket: {url} (Symbol: {toobit_sym}, Interval: {self.current_interval})")

        backoff = 1
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to Toobit live market stream: {toobit_sym} @ {self.current_interval}")
                    sub_msg = {
                        "topic": f"kline_{self.current_interval}",
                        "event": "sub",
                        "symbol": toobit_sym,
                        "params": {"binary": False}
                    }
                    await ws.send(json.dumps(sub_msg))
                    backoff = 1
                    while self._running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if "ping" in data:
                            await ws.send(json.dumps({"pong": data["ping"]}))
                            continue

                        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                            d = data["data"][0]
                            candle = {
                                "type": "candle_tick",
                                "symbol": self.current_symbol,
                                "exchange": "Toobit",
                                "time": int(d["t"] // 1000),
                                "open": float(d["o"]),
                                "high": float(d["h"]),
                                "low": float(d["l"]),
                                "close": float(d["c"]),
                                "volume": float(d.get("v", 0)),
                                "is_closed": False,
                                "interval": self.current_interval
                            }
                            await self._notify_subscribers(candle)
            except asyncio.CancelledError:
                logger.info("Toobit streaming task cancelled.")
                break
            except Exception as e:
                logger.warning(f"Toobit WebSocket error ({toobit_sym}): {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _binance_stream_loop(self):
        binance_sym = to_binance_symbol(self.current_symbol)
        stream_name = f"{binance_sym.lower()}@kline_{self.current_interval}"
        url = f"{BINANCE_FUTURES_WS_URL}/{stream_name}"
        logger.info(f"Connecting to Binance stream: {url}")

        backoff = 1
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to Binance live market stream: {stream_name}")
                    backoff = 1
                    while self._running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if "k" in data:
                            k = data["k"]
                            candle = {
                                "type": "candle_tick",
                                "symbol": self.current_symbol,
                                "exchange": "Binance",
                                "time": int(k["t"] // 1000),
                                "open": float(k["o"]),
                                "high": float(k["h"]),
                                "low": float(k["l"]),
                                "close": float(k["c"]),
                                "volume": float(k["v"]),
                                "is_closed": bool(k["x"]),
                                "interval": k["i"]
                            }
                            await self._notify_subscribers(candle)
            except asyncio.CancelledError:
                logger.info("Binance streaming task cancelled.")
                break
            except Exception as e:
                logger.warning(f"Binance WebSocket error ({stream_name}): {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

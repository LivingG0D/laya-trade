import asyncio
import json
import logging
import time
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
        # 10s timeout and connection pool to prevent connect timeouts on fluctuating latency
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=6.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)
        )
        self._ws_task: Optional[asyncio.Task] = None
        self._subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        self._running = False
        self.current_symbol = DEFAULT_SYMBOL
        self.current_interval = DEFAULT_TIMEFRAME
        self._stream_lock: asyncio.Lock = asyncio.Lock()

        # High-performance In-Memory Caching (eliminates redundant REST queries)
        self._klines_cache: Dict[str, tuple] = {}  # key -> (timestamp, List[Dict])
        self._ticker_cache: Dict[str, tuple] = {}  # key -> (timestamp, Dict)
        self._depth_cache: Dict[str, tuple] = {}   # key -> (timestamp, Dict)
        self._funding_cache: Dict[str, tuple] = {} # key -> (timestamp, float)
        self._htf_cache: Dict[str, tuple] = {}     # key -> (timestamp, Dict)

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
        Returns parsed list sorted from oldest to newest with smart 3-second cache.
        """
        normalized_limit = min(max(int(limit), 1), 1000)
        sym_key = f"{symbol.upper()}_{interval}_{normalized_limit}"
        limit = normalized_limit
        now = time.time()

        # Check TTL cache (5 seconds)
        if sym_key in self._klines_cache:
            ts, cached_klines = self._klines_cache[sym_key]
            if now - ts < 5.0 and len(cached_klines) >= min(limit, 100):
                return cached_klines[-limit:]

        klines = None
        if self.exchange == "Toobit":
            try:
                klines = await self._fetch_toobit_klines(symbol, interval, limit)
            except Exception as e:
                logger.warning(f"Toobit kline fetch failed ({e}), falling back to Binance Futures...")
                try:
                    klines = await self._fetch_binance_klines(symbol, interval, limit)
                except Exception as e2:
                    logger.error(f"Binance fallback also failed ({e2})")
        else:
            try:
                klines = await self._fetch_binance_klines(symbol, interval, limit)
            except Exception as e:
                logger.warning(f"Binance kline fetch failed ({e}), trying Toobit...")
                try:
                    klines = await self._fetch_toobit_klines(symbol, interval, limit)
                except Exception as e2:
                    logger.error(f"Toobit fallback also failed ({e2})")

        if klines and len(klines) > 0:
            self._klines_cache[sym_key] = (now, klines)
            return klines[-limit:]

        # If both failed, return existing cached data even if slightly stale
        if sym_key in self._klines_cache:
            logger.info(f"Serving stale kline cache for {sym_key} due to upstream network issue")
            return self._klines_cache[sym_key][1][-limit:]

        raise RuntimeError(f"Unable to fetch market klines for {symbol} ({interval}) from Toobit or Binance")

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

        klines_dict = {}
        for item in raw_klines:
            open_time_sec = int(item[0] // 1000)
            klines_dict[open_time_sec] = {
                "time": open_time_sec,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "taker_buy_volume": float(item[9]) if len(item) > 9 else 0.0,
                "trades": int(item[8]) if len(item) > 8 else 0,
                "is_closed": True
            }
        klines = [klines_dict[t] for t in sorted(klines_dict.keys())]
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

        klines_dict = {}
        for item in raw_klines:
            open_time_sec = int(item[0] // 1000)
            klines_dict[open_time_sec] = {
                "time": open_time_sec,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "taker_buy_volume": float(item[9]),
                "trades": int(item[8]),
                "is_closed": True
            }
        klines = [klines_dict[t] for t in sorted(klines_dict.keys())]
        return klines

    async def fetch_ticker_24h(self, symbol: str = DEFAULT_SYMBOL) -> Dict[str, Any]:
        """Fetch 24h price stats from Toobit with Binance fallback (2s cache)."""
        sym_key = symbol.upper()
        now = time.time()

        if sym_key in self._ticker_cache:
            ts, cached_ticker = self._ticker_cache[sym_key]
            if now - ts < 2.0:
                return cached_ticker

        ticker_res = None
        if self.exchange == "Toobit":
            try:
                toobit_sym = to_toobit_symbol(symbol)
                url = f"{TOOBIT_REST_URL}/quote/v1/ticker/24hr"
                params = {"symbol": toobit_sym}
                resp = await self._http_client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                d = data[0] if isinstance(data, list) and len(data) > 0 else data
                ticker_res = {
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
        if not ticker_res:
            try:
                binance_sym = to_binance_symbol(symbol)
                url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/ticker/24hr"
                params = {"symbol": binance_sym}
                resp = await self._http_client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                ticker_res = {
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

        if ticker_res and ticker_res.get("last_price", 0) > 0:
            self._ticker_cache[sym_key] = (now, ticker_res)
            return ticker_res

        # Fallback to last known ticker if available
        if sym_key in self._ticker_cache:
            return self._ticker_cache[sym_key][1]

        return {}

    async def fetch_order_book_depth(self, symbol: str = DEFAULT_SYMBOL) -> Dict[str, Any]:
        """
        Fetch order book depth (top 20 bids/asks) to compute order book imbalance.
        Positive imbalance = Bid heavy (buying pressure).
        Negative imbalance = Ask heavy (selling pressure).
        """
        now = time.time()
        sym_key = symbol.upper()
        if sym_key in self._depth_cache:
            ts, cached = self._depth_cache[sym_key]
            if now - ts < 4.0:
                return cached

        toobit_sym = to_toobit_symbol(symbol)
        url = f"{TOOBIT_REST_URL}/quote/v1/depth"
        params = {"symbol": toobit_sym, "limit": 20}
        try:
            resp = await self._http_client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                bids = data.get("b", [])
                asks = data.get("a", [])
                bid_vol = sum(float(b[1]) for b in bids if len(b) >= 2)
                ask_vol = sum(float(a[1]) for a in asks if len(a) >= 2)
                total_vol = bid_vol + ask_vol
                imbalance_pct = ((bid_vol - ask_vol) / total_vol * 100.0) if total_vol > 0 else 0.0
                ratio = (bid_vol / ask_vol) if ask_vol > 0 else 1.0
                res = {
                    "bid_volume": round(bid_vol, 2),
                    "ask_volume": round(ask_vol, 2),
                    "imbalance_pct": round(imbalance_pct, 1),
                    "bid_ask_ratio": round(ratio, 2),
                    "status": "BID_HEAVY" if imbalance_pct > 15 else ("ASK_HEAVY" if imbalance_pct < -15 else "BALANCED")
                }
                self._depth_cache[sym_key] = (now, res)
                return res
        except Exception as e:
            logger.debug(f"Depth fetch error for {symbol}: {e}")

        # Fallback neutral
        return {
            "bid_volume": 0.0,
            "ask_volume": 0.0,
            "imbalance_pct": 0.0,
            "bid_ask_ratio": 1.0,
            "status": "BALANCED"
        }

    async def fetch_funding_rate(self, symbol: str = DEFAULT_SYMBOL) -> Dict[str, Any]:
        """
        Fetch live perpetual funding rate from Binance / Toobit index.
        Returns structured dictionary with decimal and percentage formats.
        """
        now = time.time()
        sym_key = symbol.upper()
        if sym_key in self._funding_cache:
            ts, cached = self._funding_cache[sym_key]
            if now - ts < 120.0:
                return cached

        binance_sym = to_binance_symbol(symbol)
        url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/premiumIndex"
        params = {"symbol": binance_sym}
        try:
            resp = await self._http_client.get(url, params=params)
            if resp.status_code == 200:
                rate = float(resp.json().get("lastFundingRate", 0.0001))
                res = {
                    "funding_rate": rate,
                    "funding_rate_pct": round(rate * 100.0, 4)
                }
                self._funding_cache[sym_key] = (now, res)
                return res
        except Exception as e:
            logger.debug(f"Funding rate fetch error for {symbol}: {e}")

        default_res = {
            "funding_rate": 0.0001,
            "funding_rate_pct": 0.01
        }
        return default_res

    async def fetch_htf_context(self, symbol: str = DEFAULT_SYMBOL) -> Dict[str, Any]:
        """
        Fetch 1-Hour klines to evaluate macro trend structure (EMA50, EMA200, 1h RSI).
        """
        now = time.time()
        sym_key = symbol.upper()
        if sym_key in self._htf_cache:
            ts, cached = self._htf_cache[sym_key]
            if now - ts < 60.0:
                return cached

        try:
            htf_klines = await self.fetch_klines(symbol=symbol, interval="1h", limit=100)
            if len(htf_klines) >= 20:
                import numpy as np
                from .indicators import calculate_ema, calculate_rsi
                c = np.array([k["close"] for k in htf_klines], dtype=float)
                ema50 = calculate_ema(c, 50)
                ema200 = calculate_ema(c, 200) if len(c) >= 200 else None
                rsi14 = calculate_rsi(c, 14)

                cur_p = c[-1]
                e50 = ema50[-1] if not np.isnan(ema50[-1]) else cur_p
                e200 = None
                if ema200 is not None and not np.isnan(ema200[-1]):
                    e200 = float(ema200[-1])
                r14 = rsi14[-1] if not np.isnan(rsi14[-1]) else 50.0

                if e200 is None:
                    if cur_p > e50:
                        trend = "BULLISH"
                    elif cur_p < e50:
                        trend = "BEARISH"
                    else:
                        trend = "NEUTRAL"
                elif cur_p > e50 > e200:
                    trend = "STRONG_BULLISH"
                elif cur_p > e50:
                    trend = "BULLISH"
                elif cur_p < e50 < e200:
                    trend = "STRONG_BEARISH"
                elif cur_p < e50:
                    trend = "BEARISH"
                else:
                    trend = "NEUTRAL"

                res = {
                    "interval": "1h",
                    "htf_interval": "1h",
                    "trend": trend,
                    "htf_trend": trend,
                    "rsi": round(float(r14), 1),
                    "htf_rsi": round(float(r14), 1),
                    "ema50": round(float(e50), 6),
                    "htf_ema50": round(float(e50), 6),
                    "ema200": round(e200, 6) if e200 is not None else None,
                    "htf_ema200": round(e200, 6) if e200 is not None else None,
                    "is_bullish": "BULLISH" in trend,
                    "is_bearish": "BEARISH" in trend,
                }
                self._htf_cache[sym_key] = (now, res)
                return res
        except Exception as e:
            logger.debug(f"HTF context fetch error for {symbol}: {e}")

        return {
            "interval": "1h",
            "htf_interval": "1h",
            "trend": "NEUTRAL",
            "htf_trend": "NEUTRAL",
            "rsi": 50.0,
            "htf_rsi": 50.0,
            "ema50": 0.0,
            "htf_ema50": 0.0,
            "ema200": None,
            "htf_ema200": None,
            "is_bullish": False,
            "is_bearish": False,
        }

    def subscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        """Subscribe to real-time candle tick updates."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify_subscribers(self, candle_update: Dict[str, Any]):
        sym = candle_update.get("symbol", self.current_symbol).upper()
        intv = candle_update.get("interval", self.current_interval)
        cache_prefix = f"{sym}_{intv}_"

        # Keep every cached history length updated from the live candle stream.
        for cache_key in list(self._klines_cache):
            if not cache_key.startswith(cache_prefix):
                continue
            ts, klines = self._klines_cache[cache_key]
            if klines and len(klines) > 0:
                last_bar = klines[-1]
                if last_bar["time"] == candle_update["time"]:
                    last_bar["high"] = max(last_bar["high"], candle_update["high"])
                    last_bar["low"] = min(last_bar["low"], candle_update["low"])
                    last_bar["close"] = candle_update["close"]
                    last_bar["volume"] = candle_update["volume"]
                elif candle_update["time"] > last_bar["time"]:
                    klines.append({
                        "time": candle_update["time"],
                        "open": candle_update["open"],
                        "high": candle_update["high"],
                        "low": candle_update["low"],
                        "close": candle_update["close"],
                        "volume": candle_update["volume"],
                        "taker_buy_volume": candle_update.get("taker_buy_volume", 0.0),
                        "trades": candle_update.get("trades", 0),
                        "is_closed": candle_update.get("is_closed", False)
                    })
                    if len(klines) > 1000:
                        klines.pop(0)

        # Update last price in ticker cache
        if sym in self._ticker_cache:
            ts, t = self._ticker_cache[sym]
            t["last_price"] = candle_update["close"]

        for sub in list(self._subscribers):
            try:
                res = sub(candle_update)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error notifying subscriber: {e}")

    async def start_streaming(self, symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_TIMEFRAME):
        """Start or restart the background WebSocket stream for symbol and interval."""
        async with self._stream_lock:
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
        curr_intv = self.current_interval
        url = TOOBIT_WS_URL
        logger.info(f"Connecting to Toobit WebSocket: {url} (Symbol: {toobit_sym}, Interval: {curr_intv})")

        backoff = 1
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to Toobit live market stream: {toobit_sym} @ {curr_intv}")
                    sub_msg = {
                        "topic": f"kline_{curr_intv}",
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
                            topic = data.get("topic", "")
                            msg_intv = topic.replace("kline_", "") if topic.startswith("kline_") else curr_intv
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
                                "interval": msg_intv
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
        curr_intv = self.current_interval
        stream_name = f"{binance_sym.lower()}@kline_{curr_intv}"
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
                                "interval": k.get("i", curr_intv)
                            }
                            await self._notify_subscribers(candle)
            except asyncio.CancelledError:
                logger.info("Binance streaming task cancelled.")
                break
            except Exception as e:
                logger.warning(f"Binance WebSocket error ({stream_name}): {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

import asyncio
import hashlib
import hmac
import math
from decimal import Decimal
import logging
import time
from typing import Dict, Any, List, Optional
import httpx

from .config import (
    TOOBIT_REST_URL,
    DEFAULT_SYMBOL,
    DEFAULT_POSITION_MARGIN_USD,
    DEFAULT_KELLY_FRACTION_PCT
)
from .market_data import to_toobit_symbol

logger = logging.getLogger("toobit_client")


_SUCCESS_RESPONSE_CODES = {"0", "200", "00000", "success", "ok"}


def _unwrap_response_data(payload: Any) -> Any:
    """Return the useful object from legacy and envelope-style API responses."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _api_response_is_success(payload: Any) -> bool:
    """Accept Toobit's legacy success shape and reject HTTP-200 error envelopes."""
    if not isinstance(payload, dict):
        return True

    if str(payload.get("success", "")).strip().lower() == "false":
        return False

    code = payload.get("code")
    if code is not None and str(code).strip().lower() not in _SUCCESS_RESPONSE_CODES:
        return False

    status = str(payload.get("status", "")).strip().upper()
    return status not in {"ERROR", "FAILED", "REJECTED"}


def _api_response_error(payload: Any) -> str:
    """Extract a short, non-sensitive error message from an API envelope."""
    if not isinstance(payload, dict):
        return "Toobit returned an unexpected response"
    return str(
        payload.get("msg")
        or payload.get("message")
        or payload.get("error")
        or "Toobit rejected the request"
    )


def _probability_value(probabilities: Any, *keys: str, default: float = 0.0) -> float:
    """Read a probability consistently from legacy lowercase or current uppercase keys."""
    if not isinstance(probabilities, dict):
        return default
    for key in keys:
        value = probabilities.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return min(1.0, max(0.0, numeric))
    return default


def _confidence_percent(value: Any = None, score: Any = None) -> float:
    """Normalize model confidence to a 0-100 display percentage."""
    candidate = value
    if candidate is None:
        candidate = score
        try:
            numeric_score = float(candidate)
            if 1.0 <= numeric_score <= 4.0:
                candidate = numeric_score / 4.0
        except (TypeError, ValueError):
            return 50.0
    try:
        numeric = float(candidate)
    except (TypeError, ValueError):
        return 50.0
    if not math.isfinite(numeric):
        return 50.0
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0
    return min(100.0, max(0.0, numeric))


def _position_side_from_order_side(side: str) -> Optional[str]:
    """Map an order direction to the position direction it protects."""
    normalized = str(side or "").strip().upper()
    if normalized in {"LONG", "BUY", "BUY_OPEN", "SELL_CLOSE"}:
        return "LONG"
    if normalized in {"SHORT", "SELL", "SELL_OPEN", "BUY_CLOSE"}:
        return "SHORT"
    return None


def _response_order_id(payload: Any) -> Optional[str]:
    """Extract an order identifier from legacy or envelope-style responses."""
    data = _unwrap_response_data(payload)
    if not isinstance(data, dict):
        return None
    for key in ("orderId", "order_id", "origClientOrderId", "clientOrderId", "newClientOrderId"):
        value = data.get(key)
        if value not in (None, "", 0, "0"):
            return str(value)
    return None


def _response_matches_tpsl(
    payload: Any,
    position_side: str,
    stop_loss: float,
    take_profit: float,
) -> bool:
    """Verify that Toobit echoed the requested position-level protection prices."""
    data = _unwrap_response_data(payload)
    if not isinstance(data, dict):
        return False

    response_side = str(data.get("side", "")).strip().upper()
    if response_side and response_side != position_side:
        return False

    requested_values = (("stopLoss", stop_loss), ("takeProfit", take_profit))
    for key, requested in requested_values:
        if requested <= 0:
            continue
        if key not in data:
            return False
        try:
            actual = float(data.get(key) or 0)
        except (TypeError, ValueError):
            return False
        if actual <= 0 or abs(actual - requested) > max(1e-8, abs(requested) * 1e-8):
            return False
    return True


def _default_demo_targets(
    entry_price: float,
    side: str,
    leverage: int,
) -> tuple[float, float, float]:
    """Build conservative demo-only protection targets when none were supplied."""
    if entry_price <= 0:
        return 0.0, 0.0, 0.0
    normalized_leverage = max(float(leverage), 1.0)
    risk_distance = 0.25 / normalized_leverage
    tp1_distance = 0.40 / normalized_leverage
    tp2_distance = 0.60 / normalized_leverage
    if str(side).upper() == "SHORT":
        return (
            entry_price * (1.0 + risk_distance),
            entry_price * (1.0 - tp1_distance),
            entry_price * (1.0 - tp2_distance),
        )
    return (
        entry_price * (1.0 - risk_distance),
        entry_price * (1.0 + tp1_distance),
        entry_price * (1.0 + tp2_distance),
    )


def _price_to_exchange_precision(price: float, tick_size: float) -> tuple[str, float]:
    """Quantize a price without ever converting a positive target to zero."""
    if price <= 0:
        return "", 0.0
    if tick_size > 0:
        tick_text = f"{tick_size:.12f}".rstrip("0").rstrip(".")
        decimals = len(tick_text.split(".")[1]) if "." in tick_text else 0
        formatted = f"{price:.{min(decimals, 12)}f}"
        if float(formatted) > 0:
            return formatted, float(formatted)
    formatted = f"{price:.12f}".rstrip("0").rstrip(".")
    return formatted, float(formatted)


class ToobitClient:
    """
    Client for interacting with Toobit's Perpetual Futures API.
    Supports live authenticated accounts (HMAC-SHA256) and simulated demo mode.
    """

    def __init__(self, api_key: str = "", api_secret: str = "", is_demo: bool = False):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.is_demo = is_demo
        self.base_url = TOOBIT_REST_URL
        self._time_offset: int = 0  # Milliseconds offset: server_time - local_time
        self._last_time_sync: float = 0.0
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0, read=15.0),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=15, keepalive_expiry=30.0)
        )

        # Demo mode state storage
        self._demo_balance = 5000.0  # $5,000 USDT virtual starting margin
        self._demo_positions: Dict[str, Dict[str, Any]] = {}
        self._last_positions: List[Dict[str, Any]] = []  # last live positions fetched
        self._position_tpsl_cache: Dict[str, Dict[str, Any]] = {}  # local cache of configured TP/SL
        self._contract_rules_cache: Dict[str, Dict[str, Any]] = {}
        self._last_rules_cache_time: float = 0.0
        
        # If demo mode is default or requested without keys, initialize a default demo position for 1000PEPE
        if is_demo:
            self._init_default_demo_position()

    def _init_default_demo_position(self):
        """Seed an initial demo position for 1000PEPE so users can test immediately."""
        toobit_sym = to_toobit_symbol(DEFAULT_SYMBOL)
        default_sl, default_tp1, default_tp2 = _default_demo_targets(0.004180, "LONG", 50)
        self._demo_positions[toobit_sym] = {
            "symbol": toobit_sym,
            "displaySymbol": DEFAULT_SYMBOL,
            "side": "LONG",
            "positionSide": "LONG",
            "quantity": 100000.0,
            "entryPrice": 0.004180,
            "markPrice": 0.004245,
            "leverage": 50,
            "margin": 8.36,
            "unrealizedPnl": 6.50,
            "pnlPercentage": 77.75,
            "liquidationPrice": 0.004105,
            "stopLoss": default_sl,
            "takeProfit1": default_tp1,
            "takeProfit2": default_tp2,
            "isolated": True,
            "createdAt": int(time.time() * 1000)
        }

    @property
    def is_connected(self) -> bool:
        return bool(self.api_key and self.api_secret) or self.is_demo

    def set_credentials(self, api_key: str, api_secret: str, is_demo: bool = False):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.is_demo = is_demo
        if is_demo and not self._demo_positions:
            self._init_default_demo_position()
        logger.info(f"ToobitClient configured: demo={self.is_demo}, has_keys={bool(self.api_key)}")

    def disconnect(self):
        self.api_key = ""
        self.api_secret = ""
        self.is_demo = False
        self._demo_positions.clear()
        self._last_positions = []
        logger.info("ToobitClient disconnected and session cleared")

    def _build_query_string(self, params: Dict[str, Any]) -> str:
        """Serialize params in insertion order - the exact bytes we sign and send."""
        return "&".join(f"{k}={v}" for k, v in params.items())

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA-256 signature from the exact query string that is sent.

        Toobit requires the parameter order in the request to match the order used
        for signature calculation, so params are never sorted here - we sign the
        same bytes that go on the wire.
        """
        query_string = self._build_query_string(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def sync_server_time(self) -> int:
        """Fetch Toobit server time and calculate clock offset."""
        for attempt in range(2):
            try:
                resp = await self._http_client.get(f"{self.base_url}/api/v1/time", timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    server_time = int(data.get("serverTime", 0))
                    if server_time > 0:
                        local_time = int(time.time() * 1000)
                        self._time_offset = server_time - local_time
                        self._last_time_sync = time.time()
                        logger.info(f"Toobit server time synchronized: offset={self._time_offset}ms")
                        return self._time_offset
            except Exception as e:
                logger.warning(f"Failed to sync server time with Toobit (attempt {attempt+1}): {repr(e)}")
                if attempt == 0:
                    await asyncio.sleep(0.5)
        return self._time_offset

    async def _send_signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        retry_on_ts: bool = True
    ) -> Dict[str, Any]:
        """Send a signed HTTP request to Toobit API conforming to official specifications."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Toobit API Key and Secret are required for live operations.")

        # Ensure server time offset is synchronized
        if self._last_time_sync == 0 or (time.time() - self._last_time_sync > 900):
            await self.sync_server_time()

        req_params = dict(params or {})
        # Timestamp synchronized with Toobit's server clock
        req_params["timestamp"] = int(time.time() * 1000) + self._time_offset
        if "recvWindow" not in req_params:
            req_params["recvWindow"] = 60000

        # Sign the query string in exact insertion order
        query_string = self._build_query_string(req_params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        req_params["signature"] = signature
        full_query_string = self._build_query_string(req_params)

        headers = {
            "X-BB-APIKEY": self.api_key,
            "User-Agent": "TooBit-SDK/1.0.0"
        }

        m = method.upper()
        if m == "GET":
            url = f"{self.base_url}{endpoint}?{full_query_string}"
            resp = await self._http_client.get(url, headers=headers)
        elif m == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            url = f"{self.base_url}{endpoint}"
            resp = await self._http_client.post(url, headers=headers, content=full_query_string)
        elif m == "DELETE":
            url = f"{self.base_url}{endpoint}?{full_query_string}"
            resp = await self._http_client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        if resp.status_code != 200:
            logger.error(f"Toobit API error [{resp.status_code}] {method} {endpoint}: {resp.text}")
            try:
                err_data = resp.json()
                err_msg = err_data.get("msg") or err_data.get("message") or resp.text
                err_code = err_data.get("code")
            except Exception:
                err_msg = resp.text
                err_code = None

            # Retry once if timestamp outside recvWindow (-1021) by forcing clock resync
            if err_code == -1021 and retry_on_ts:
                logger.info("Resyncing Toobit server clock after -1021 error and retrying...")
                await self.sync_server_time()
                return await self._send_signed_request(method, endpoint, params, retry_on_ts=False)

            if err_code is not None:
                err_msg = f"[{err_code}] {err_msg}"
            raise RuntimeError(f"Toobit API Error ({resp.status_code}): {err_msg}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError("Toobit returned a non-JSON response") from exc

        if not _api_response_is_success(payload):
            raise RuntimeError(f"Toobit API rejected the request: {_api_response_error(payload)}")

        return payload

    async def test_connection(self) -> Dict[str, Any]:
        """Test API credentials with thorough validation and diagnostics."""
        if self.is_demo:
            return {
                "success": True,
                "mode": "demo",
                "message": "Connected to Simulated Demo Account (Virtual $5,000 USDT Margin)."
            }

        if not self.api_key or not self.api_secret:
            return {
                "success": False,
                "mode": "disconnected",
                "message": "API Key or Secret is missing."
            }

        # 1. Sync clock with Toobit server
        await self.sync_server_time()

        # 2. Test futures balance endpoint
        try:
            balance_data = await self._send_signed_request("GET", "/api/v1/futures/balance")
            return {
                "success": True,
                "mode": "live",
                "message": "Successfully authenticated with Toobit Futures API!",
                "data": balance_data
            }
        except Exception as e:
            err_str = str(e).strip()
            if not err_str or "timeout" in type(e).__name__.lower():
                err_str = (
                    "Network Timeout: Connection to Toobit API (api.toobit.com) timed out.\n"
                    "• Please check your internet connection or VPN/proxy.\n"
                    "• Timeouts have been extended, please click 'Connect & Validate API' again."
                )
            logger.warning(f"Toobit futures connection test failed: {err_str}")

            # If error is -1000 or 400, test spot account endpoint to see if key works for spot
            if "[-1000]" in err_str or "400" in err_str:
                try:
                    spot_res = await self._send_signed_request("GET", "/api/v1/account")
                    if spot_res:
                        return {
                            "success": False,
                            "mode": "error",
                            "message": (
                                "API Key connected successfully to Spot, but Toobit rejected Futures access (Error -1000).\n"
                                "• Please verify that 'Futures Trading' or 'Read' permission is checked in your Toobit API settings.\n"
                                "• Ensure you have activated/opened Futures contract trading in your Toobit dashboard."
                            )
                        }
                except Exception:
                    pass

                return {
                    "success": False,
                    "mode": "error",
                    "message": (
                        "Toobit API Error (-1000): Authentication could not be completed on Toobit.\n"
                        "• Check that API Key & Secret were copied without leading/trailing spaces.\n"
                        "• Ensure 'Futures Trading' permission is enabled for this API key on Toobit.\n"
                        "• If you configured an IP Whitelist on Toobit, ensure your current IP is added.\n"
                        "• Make sure Futures trading has been activated on your Toobit account."
                    )
                }
            elif "[-1022]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "Signature Error (-1022): The Secret Key is incorrect. Please verify your Toobit Secret Key."
                }
            elif "[-1107]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "API Key Error (-1107): The API Key is missing or invalid format. Please check your API Key."
                }
            elif "[-1021]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "Timestamp Error (-1021): Request outside recvWindow. Clock was resynced, please retry."
                }
            elif "[-1023]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "IP Restriction (-1023): Your IP address is not whitelisted in Toobit API Management."
                }
            elif "[-2015]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "Permission Error (-2015): Invalid API-key, IP, or permissions for futures action."
                }
            elif "[-2017]" in err_str:
                return {
                    "success": False,
                    "mode": "error",
                    "message": "API Key Expired (-2017): Your API key has expired. Please generate a new key on Toobit."
                }

            return {
                "success": False,
                "mode": "error",
                "message": err_str
            }

    async def get_balance(self) -> Dict[str, Any]:
        """Fetch account balance details."""
        if self.is_demo:
            # Calculate total demo equity including unrealized PnL
            unrealized_total = sum(p.get("unrealizedPnl", 0.0) for p in self._demo_positions.values())
            total_margin = sum(p.get("margin", 0.0) for p in self._demo_positions.values())
            free_margin = max(0.0, self._demo_balance - total_margin)
            equity = self._demo_balance + unrealized_total

            return {
                "mode": "demo",
                "asset": "USDT",
                "totalBalance": round(self._demo_balance, 2),
                "availableBalance": round(free_margin, 2),
                "marginBalance": round(total_margin, 2),
                "unrealizedPnl": round(unrealized_total, 2),
                "totalEquity": round(equity, 2)
            }

        try:
            raw_balances = await self._send_signed_request("GET", "/api/v1/futures/balance")
            usdt_bal = {}
            if isinstance(raw_balances, list):
                for b in raw_balances:
                    if b.get("coin") == "USDT" or b.get("asset") == "USDT":
                        usdt_bal = b
                        break
            elif isinstance(raw_balances, dict):
                usdt_bal = raw_balances

            total_balance = float(usdt_bal.get("balance") or usdt_bal.get("totalWalletBalance") or 0.0)
            unrealized = float(usdt_bal.get("crossUnRealizedPnl") or usdt_bal.get("unrealizedProfit") or 0.0)
            available = float(usdt_bal.get("availableBalance") or usdt_bal.get("availableMargin") or 0.0)
            margin = float(usdt_bal.get("positionMargin") or 0.0)
            equity = float(usdt_bal.get("equity") or (total_balance + unrealized))

            return {
                "mode": "live",
                "asset": "USDT",
                "totalBalance": round(total_balance, 2),
                "availableBalance": round(available, 2),
                "marginBalance": round(margin, 2),
                "unrealizedPnl": round(unrealized, 2),
                "totalEquity": round(equity, 2)
            }
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return {
                "mode": "live",
                "error": str(e),
                "asset": "USDT",
                "totalBalance": 0.0,
                "availableBalance": 0.0
            }

    async def get_positions(self, symbol: Optional[str] = None, current_market_price: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Fetch active open positions.
        Filters out positions with size == 0.
        """
        if self.is_demo:
            positions = []
            for sym, pos in list(self._demo_positions.items()):
                # If current price is provided, update live PnL dynamically
                if current_market_price and current_market_price > 0:
                    pos["markPrice"] = current_market_price
                    entry = pos["entryPrice"]
                    qty = pos["quantity"]
                    side = pos["side"]

                    if side == "LONG":
                        pnl = (current_market_price - entry) * qty
                        pnl_pct = ((current_market_price - entry) / entry) * 100 * pos["leverage"]
                    else:
                        pnl = (entry - current_market_price) * qty
                        pnl_pct = ((entry - current_market_price) / entry) * 100 * pos["leverage"]

                    pos["unrealizedPnl"] = round(pnl, 2)
                    pos["pnlPercentage"] = round(pnl_pct, 2)

                if symbol:
                    toobit_sym = to_toobit_symbol(symbol)
                    if sym == toobit_sym:
                        positions.append(dict(pos))
                else:
                    positions.append(dict(pos))
            return positions

        try:
            # Query all active positions; /api/v1/futures/positions does not take symbol parameter
            raw_positions = await self._send_signed_request("GET", "/api/v1/futures/positions")
            active_positions = []
            target_toobit_sym = to_toobit_symbol(symbol) if symbol else None

            if isinstance(raw_positions, list):
                for p in raw_positions:
                    p_sym = p.get("symbol", "")
                    if symbol:
                        norm_target = symbol.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
                        norm_pos = p_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
                        if norm_target != norm_pos and p_sym != target_toobit_sym:
                            continue

                    # Toobit position fields: position (size), avgPrice (entry),
                    # flp (liquidation price), unrealizedPnL, markPrice, marginType
                    qty = float(p.get("position", p.get("quantity", p.get("positionAmt", p.get("size", 0.0)))))
                    if abs(qty) > 0:
                        side = (p.get("side") or p.get("positionSide") or ("LONG" if qty > 0 else "SHORT")).upper()
                        entry_price = float(p.get("avgPrice", p.get("entryPrice", 0.0)))
                        mark_price = float(p.get("markPrice") or p.get("lastPrice") or current_market_price or entry_price)
                        unrealized_pnl = float(p.get("unrealizedPnL", p.get("unrealizedPnl", p.get("unRealizedProfit", 0.0))))
                        leverage = int(float(p.get("leverage", 10)))
                        liq_price = float(p.get("flp", p.get("liquidationPrice", 0.0)))
                        multiplier = float(p.get("contractMultiplier") or (self._contract_rules_cache.get(p_sym, {}).get("multiplier", 1.0)))
                        margin = float(p.get("margin") or ((abs(qty) * entry_price * multiplier) / max(leverage, 1)))

                        pnl_pct = 0.0
                        if margin > 0:
                            pnl_pct = (unrealized_pnl / margin) * 100

                        clean_sym = p_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
                        canonical_disp = f"{clean_sym}.P" if clean_sym.endswith("USDT") else f"{clean_sym}USDT.P"

                        tpsl_info = self._position_tpsl_cache.get(p_sym, {})
                        sl_val = float(p.get("stopLoss") or tpsl_info.get("stopLoss", 0.0))
                        tp_val = float(p.get("takeProfit") or p.get("takeProfit1") or tpsl_info.get("takeProfit", 0.0))

                        active_positions.append({
                            "symbol": p_sym,
                            "rawSymbol": p_sym,
                            "displaySymbol": symbol if symbol else canonical_disp,
                            "side": side,
                            "positionSide": side,
                            "quantity": abs(qty),
                            "entryPrice": entry_price,
                            "markPrice": mark_price,
                            "leverage": leverage,
                            "margin": round(margin, 2),
                            "unrealizedPnl": round(unrealized_pnl, 2),
                            "pnlPercentage": round(pnl_pct, 2),
                            "liquidationPrice": liq_price,
                            "isolated": str(p.get("marginType", "ISOLATED")).upper() != "CROSS",
                            "stopLoss": sl_val,
                            "takeProfit": tp_val,
                            "takeProfit1": tp_val
                        })

            self._last_positions = active_positions
            return active_positions
        except Exception as e:
            logger.error(f"Error fetching Toobit positions: {e}")
            raise

    async def _resolve_position_side(self, symbol: str) -> Optional[str]:
        """flashClose requires the position side (LONG/SHORT) - find it on the account."""
        toobit_sym = to_toobit_symbol(symbol)
        for p in self._last_positions:
            if p.get("symbol") == toobit_sym:
                return p.get("side")

        positions = await self.get_positions(symbol=symbol)
        if positions:
            return positions[0].get("side")
        return None

    async def flash_close(self, symbol: str) -> Dict[str, Any]:
        """
        Close position immediately at market price using flashClose endpoint.
        """
        toobit_sym = to_toobit_symbol(symbol)

        if self.is_demo:
            if toobit_sym in self._demo_positions:
                closed = self._demo_positions.pop(toobit_sym)
                pnl = closed.get("unrealizedPnl", 0.0)
                self._demo_balance += pnl
                return {
                    "success": True,
                    "mode": "demo",
                    "symbol": toobit_sym,
                    "closedPosition": closed,
                    "realizedPnl": pnl,
                    "message": f"Successfully Flash Closed simulated position {toobit_sym} (PnL: {'+' if pnl >= 0 else ''}${pnl:.2f})"
                }
            else:
                return {
                    "success": False,
                    "mode": "demo",
                    "symbol": toobit_sym,
                    "message": f"No open demo position found for {toobit_sym}"
                }

        try:
            side = await self._resolve_position_side(symbol)
            if not side:
                return {
                    "success": False,
                    "mode": "live",
                    "symbol": toobit_sym,
                    "message": f"No open Toobit position found for {toobit_sym}."
                }

            params = {
                "symbol": toobit_sym,
                "side": side
            }
            res = await self._send_signed_request("POST", "/api/v1/futures/flashClose", params)
            return {
                "success": True,
                "mode": "live",
                "symbol": toobit_sym,
                "data": res,
                "message": f"Flash Close order executed on Toobit for {toobit_sym}!"
            }
        except Exception as e:
            logger.warning(f"Error executing flashClose on {toobit_sym} ({e}), attempting market close fallback...")
            try:
                positions = await self.get_positions(symbol=symbol)
                if positions:
                    pos = positions[0]
                    qty = float(pos.get("quantity", 0.0))
                    pos_side = str(pos.get("side", "")).upper()
                    exit_side = "SELL_CLOSE" if pos_side == "LONG" else "BUY_CLOSE"
                    if qty > 0:
                        close_res = await self.create_order(
                            symbol=symbol,
                            side=exit_side,
                            order_type="MARKET",
                            quantity=qty
                        )
                        if close_res.get("success"):
                            return {
                                "success": True,
                                "mode": "live",
                                "symbol": toobit_sym,
                                "data": close_res,
                                "message": f"Closed {toobit_sym} position via market fallback order!"
                            }
            except Exception as e2:
                logger.error(f"Fallback market close failed for {toobit_sym}: {e2}")

            logger.error(f"Error executing flash close on {toobit_sym}: {e}")
            return {
                "success": False,
                "mode": "live",
                "symbol": toobit_sym,
                "error": str(e),
                "message": f"Failed to Flash Close {toobit_sym}: {str(e)}"
            }

    def simulate_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        leverage: int = 50,
        stop_loss: float = 0.0,
        take_profit_1: float = 0.0,
        take_profit_2: float = 0.0
    ) -> Dict[str, Any]:
        """Open or replace a simulated demo position for testing."""
        toobit_sym = to_toobit_symbol(symbol)
        side_norm = side.upper()
        if side_norm not in ("LONG", "SHORT"):
            side_norm = "LONG"

        default_sl, default_tp1, default_tp2 = _default_demo_targets(
            entry_price,
            side_norm,
            leverage,
        )
        stop_loss = stop_loss if stop_loss > 0 else default_sl
        take_profit_1 = take_profit_1 if take_profit_1 > 0 else default_tp1
        take_profit_2 = take_profit_2 if take_profit_2 > 0 else default_tp2

        margin = (quantity * entry_price) / max(leverage, 1)

        # Estimate liquidation price (approx ~ 85-90% margin depletion)
        if side_norm == "LONG":
            liq_price = entry_price * (1.0 - (0.9 / leverage))
        else:
            liq_price = entry_price * (1.0 + (0.9 / leverage))

        pos = {
            "symbol": toobit_sym,
            "displaySymbol": symbol,
            "side": side_norm,
            "positionSide": side_norm,
            "quantity": quantity,
            "entryPrice": entry_price,
            "markPrice": entry_price,
            "leverage": leverage,
            "margin": round(margin, 2),
            "unrealizedPnl": 0.0,
            "pnlPercentage": 0.0,
            "liquidationPrice": round(liq_price, 6),
            "stopLoss": stop_loss,
            "takeProfit1": take_profit_1,
            "takeProfit2": take_profit_2,
            "isolated": True,
            "createdAt": int(time.time() * 1000)
        }
        self._demo_positions[toobit_sym] = pos
        return pos

    async def get_symbol_rules(self, symbol: str) -> Dict[str, Any]:
        """Fetch trading rules, step size, and tick precision for a contract symbol."""
        toobit_sym = to_toobit_symbol(symbol)
        now = time.time()
        if not self._contract_rules_cache or (now - self._last_rules_cache_time > 3600):
            try:
                resp = await self._http_client.get(f"{self.base_url}/api/v1/exchangeInfo", timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    contracts = data.get("contracts", [])
                    rules_map = {}
                    for c in contracts:
                        sym = c.get("symbol")
                        if not sym:
                            continue
                        step_size = Decimal("0.001")
                        tick_size = Decimal("0.01")
                        min_qty = Decimal("0.001")
                        max_qty = Decimal("1000000")
                        min_notional = Decimal("5.0")

                        for f in c.get("filters", []):
                            ft = f.get("filterType")
                            if ft == "LOT_SIZE":
                                min_qty = Decimal(str(f.get("minQty", min_qty)))
                                max_qty = Decimal(str(f.get("maxQty", max_qty)))
                                step_size = Decimal(str(f.get("stepSize", step_size)))
                            elif ft == "PRICE_FILTER":
                                tick_size = Decimal(str(f.get("tickSize", tick_size)))
                            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                                min_notional = Decimal(str(f.get("minNotional", min_notional)))

                        min_trade_prec = Decimal(str(c.get("minTradePrecision", "0") or "0"))
                        if min_trade_prec > 0:
                            min_qty = max(min_qty, min_trade_prec)
                            step_size = max(step_size, min_trade_prec)

                        rules_map[sym] = {
                            "symbol": sym,
                            "step_size": float(step_size),
                            "tick_size": float(tick_size),
                            "min_qty": float(min_qty),
                            "max_qty": float(max_qty),
                            "min_notional": float(min_notional),
                            "multiplier": float(c.get("contractMultiplier", 1.0))
                        }
                    self._contract_rules_cache = rules_map
                    self._last_rules_cache_time = now
            except Exception as e:
                logger.warning(f"Error refreshing Toobit contract rules: {e}")

        if toobit_sym in self._contract_rules_cache:
            return self._contract_rules_cache[toobit_sym]

        return {
            "symbol": toobit_sym,
            "step_size": 0.001,
            "tick_size": 0.01,
            "min_qty": 0.001,
            "max_qty": 100000.0,
            "min_notional": 5.0,
            "multiplier": 1.0
        }

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Configure leverage multiplier on Toobit futures."""
        toobit_sym = to_toobit_symbol(symbol)
        if self.is_demo:
            if toobit_sym in self._demo_positions:
                self._demo_positions[toobit_sym]["leverage"] = leverage
            return {"success": True, "mode": "demo", "symbol": toobit_sym, "leverage": leverage}

        params = {"symbol": toobit_sym, "leverage": leverage}
        try:
            res = await self._send_signed_request("POST", "/api/v1/futures/leverage", params)
            return {"success": True, "mode": "live", "symbol": toobit_sym, "leverage": leverage, "data": res}
        except Exception as e:
            if "already" in str(e).lower() or "not modified" in str(e).lower():
                return {"success": True, "mode": "live", "symbol": toobit_sym, "leverage": leverage}
            logger.warning(f"Could not set leverage on {toobit_sym}: {e}")
            return {"success": False, "mode": "live", "symbol": toobit_sym, "error": str(e)}

    async def get_symbol_leverage_config(self, symbol: str) -> Dict[str, Any]:
        """Read the leverage and margin mode the account already uses for a symbol."""
        toobit_sym = to_toobit_symbol(symbol)
        if self.is_demo:
            return {"success": True, "mode": "demo", "symbol": toobit_sym, "marginType": None, "leverage": None}

        try:
            res = await self._send_signed_request("GET", "/api/v1/futures/accountLeverage", {"symbol": toobit_sym})
        except Exception as e:
            logger.warning(f"Could not read margin config for {toobit_sym}: {e}")
            return {"success": False, "mode": "live", "symbol": toobit_sym, "error": str(e)}

        if isinstance(res, list):
            rows: List[Any] = res
        elif isinstance(res, dict):
            data = res.get("data")
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = [data]
            else:
                rows = [res]
        else:
            rows = []

        row = None
        for candidate in rows:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("symbolId") or candidate.get("symbol") or "").upper() == toobit_sym.upper():
                row = candidate
                break
        if row is None:
            row = next((c for c in rows if isinstance(c, dict)), None)

        return {
            "success": True,
            "mode": "live",
            "symbol": toobit_sym,
            "marginType": str((row or {}).get("marginType") or "").upper() or None,
            "leverage": (row or {}).get("leverage"),
        }

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Configure margin mode: ISOLATED or CROSSED."""
        toobit_sym = to_toobit_symbol(symbol)
        if self.is_demo:
            return {"success": True, "mode": "demo", "symbol": toobit_sym, "marginType": margin_type}

        params = {"symbol": toobit_sym, "marginType": margin_type.upper()}
        try:
            res = await self._send_signed_request("POST", "/api/v1/futures/marginType", params)
            return {"success": True, "mode": "live", "symbol": toobit_sym, "marginType": margin_type, "data": res}
        except Exception as e:
            if "already" in str(e).lower() or "-4046" in str(e) or "4046" in str(e):
                return {"success": True, "mode": "live", "symbol": toobit_sym, "marginType": margin_type, "message": "Already set"}
            logger.warning(f"Could not set margin type on {toobit_sym}: {e}")
            return {"success": False, "mode": "live", "symbol": toobit_sym, "error": str(e)}

    async def _ensure_isolated_margin(self, symbol: str) -> Dict[str, Any]:
        """Confirm isolated margin for a live entry, writing only when it is needed.

        Toobit refuses a margin-mode change while a position or open order exists
        (-4047/-4048), so an already-isolated symbol is detected instead of having
        the rejected change re-issued on every entry.
        """
        toobit_sym = to_toobit_symbol(symbol)
        config = await self.get_symbol_leverage_config(toobit_sym)
        current = config.get("marginType") if config.get("success") else None
        if current == "ISOLATED":
            return {
                "success": True,
                "mode": "live",
                "symbol": toobit_sym,
                "marginType": "ISOLATED",
                "message": "Already set",
            }

        result = await self.set_margin_type(toobit_sym, "ISOLATED")
        if not result.get("success") and current == "CROSS":
            reason = result.get("error") or result.get("message") or "isolated margin setup was not confirmed"
            result = dict(result)
            result.pop("message", None)
            result["error"] = (
                f"{reason} | {toobit_sym} is still cross-margined: close the position "
                f"or switch it to isolated on the exchange first"
            )
        return result

    async def _attach_entry_protection(
        self,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
    ) -> Dict[str, Any]:
        """Place one position bracket and an optional separately verified TP2."""
        normalized_side = _position_side_from_order_side(position_side)
        if normalized_side is None:
            return {
                "success": False,
                "bracket": [],
                "errors": [f"Invalid position side: {position_side}"],
            }
        if quantity <= 0:
            return {
                "success": False,
                "bracket": [],
                "errors": ["A positive position quantity is required for protection."],
            }
        if stop_loss <= 0:
            return {
                "success": False,
                "bracket": [],
                "errors": ["A positive stop-loss is required for live entries."],
            }

        has_tp1 = take_profit_1 > 0
        has_tp2 = take_profit_2 > 0
        tp1_quantity = quantity / 2.0 if has_tp1 and has_tp2 else quantity
        tp2_quantity = quantity - tp1_quantity if has_tp2 else 0.0
        bracket: List[Dict[str, Any]] = []
        errors: List[str] = []

        primary_result = await self.set_position_tpsl(
            symbol=symbol,
            side=normalized_side,
            stop_loss=stop_loss,
            take_profit=take_profit_1 if has_tp1 else 0.0,
            stop_loss_size=quantity,
            take_profit_size=tp1_quantity if has_tp1 else None,
        )
        bracket.append({
            "type": "SL+TP1" if has_tp1 else "SL",
            "quantity": quantity,
            "stopLoss": primary_result.get("stopLoss", stop_loss),
            "takeProfit": primary_result.get("takeProfit", take_profit_1 if has_tp1 else 0.0),
            "res": primary_result,
        })
        if not primary_result.get("success"):
            errors.append(
                f"position bracket: {primary_result.get('message', 'not confirmed')}"
            )

        if not errors and has_tp2:
            exit_side = "SELL_CLOSE" if normalized_side == "LONG" else "BUY_CLOSE"
            tp2_result = await self.create_plan_order(
                symbol=symbol,
                side=exit_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=tp2_quantity,
                trigger_price=take_profit_2,
                reduce_only=True,
                position_side=normalized_side,
                take_profit_slot="takeProfit2",
            )
            bracket.append({
                "type": "TP2",
                "quantity": tp2_quantity,
                "price": take_profit_2,
                "res": tp2_result,
            })
            if not tp2_result.get("success"):
                errors.append(f"TP2: {tp2_result.get('message', 'not confirmed')}")

        return {"success": not errors, "bracket": bracket, "errors": errors}

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: float = 0.0,
        price: Optional[float] = None,
        leverage: int = 50,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit_1: float = 0.0,
        take_profit_2: float = 0.0
    ) -> Dict[str, Any]:
        """
        Execute an order on Toobit futures.
        Side: BUY (Long), SELL (Short).
        Supports live authenticated orders and simulated demo execution.
        """
        toobit_sym = to_toobit_symbol(symbol)
        side_norm = side.upper()
        if side_norm in ("LONG", "BUY", "BUY_OPEN"):
            order_side = "BUY"
            pos_side = "LONG"
            toobit_side = "BUY_OPEN"
        elif side_norm in ("BUY_CLOSE",):
            order_side = "BUY"
            pos_side = "LONG"
            toobit_side = "BUY_CLOSE"
        elif side_norm in ("SELL_CLOSE",):
            order_side = "SELL"
            pos_side = "SHORT"
            toobit_side = "SELL_CLOSE"
        else:
            order_side = "SELL"
            pos_side = "SHORT"
            toobit_side = "SELL_OPEN"

        if self.is_demo:
            pos = self.simulate_position(
                symbol=symbol,
                side=pos_side,
                quantity=quantity,
                entry_price=entry_price,
                leverage=leverage,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2
            )
            return {
                "success": True,
                "mode": "demo",
                "orderId": f"demo_{int(time.time() * 1000)}",
                "symbol": toobit_sym,
                "side": order_side,
                "quantity": quantity,
                "price": entry_price,
                "position": pos
            }

        try:
            # 1. Set leverage and margin mode before any live entry is dispatched
            if "CLOSE" not in toobit_side:
                margin_result = await self._ensure_isolated_margin(toobit_sym)
                if not margin_result or not margin_result.get("success"):
                    message = (
                        (margin_result or {}).get("message")
                        or (margin_result or {}).get("error")
                        or "isolated margin setup was not confirmed"
                    )
                    return {
                        "success": False,
                        "mode": "live",
                        "symbol": toobit_sym,
                        "message": f"Margin setup failed: {message}",
                    }

                leverage_result = await self.set_leverage(toobit_sym, leverage)
                if not leverage_result or not leverage_result.get("success"):
                    message = (
                        (leverage_result or {}).get("message")
                        or (leverage_result or {}).get("error")
                        or "leverage setup was not confirmed"
                    )
                    return {
                        "success": False,
                        "mode": "live",
                        "symbol": toobit_sym,
                        "message": f"Leverage setup failed: {message}",
                    }

            # 2. Prepare order params
            is_market = order_type.upper() == "MARKET"
            params = {
                "symbol": toobit_sym,
                "side": toobit_side,
                "type": "LIMIT",
                "priceType": "MARKET" if is_market else "LIMIT",
                "quantity": str(quantity),
                "newClientOrderId": f"laya_{int(time.time() * 1000)}",
            }
            if is_market:
                params["timeInForce"] = "IOC"
            else:
                params["price"] = str(price if price else entry_price)
                params["timeInForce"] = "GTC"

            # Execute order on Toobit futures endpoint
            try:
                res = await self._send_signed_request("POST", "/api/v1/futures/order", params)
            except Exception as e:
                # If BUY_OPEN/SELL_OPEN fails because exchange expects standard BUY/SELL
                if "side" in str(e).lower():
                    params["side"] = order_side
                    res = await self._send_signed_request("POST", "/api/v1/futures/order", params)
                else:
                    raise e

            # 3. Attach and verify protective orders before reporting success
            protection = await self._attach_entry_protection(
                symbol=toobit_sym,
                position_side=pos_side,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
            )
            if not protection["success"]:
                cleanup = None
                try:
                    cleanup = await self.flash_close(toobit_sym)
                except Exception as exc:
                    cleanup = {"success": False, "message": str(exc)}
                return {
                    "success": False,
                    "mode": "live",
                    "symbol": toobit_sym,
                    "message": "Protective orders could not be confirmed: " + "; ".join(protection["errors"]),
                    "data": res,
                    "bracket": protection["bracket"],
                    "cleanup": cleanup,
                }

            return {
                "success": True,
                "mode": "live",
                "symbol": toobit_sym,
                "side": order_side,
                "quantity": quantity,
                "data": res,
                "bracket": protection["bracket"],
            }
        except Exception as e:
            logger.error(f"Failed to create order on {toobit_sym}: {e}")
            return {
                "success": False,
                "mode": "live",
                "symbol": toobit_sym,
                "error": str(e),
                "message": f"Order placement failed: {str(e)}"
            }

    async def set_position_tpsl(
        self,
        symbol: str,
        side: str,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        stop_loss_size: Optional[float] = None,
        take_profit_size: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Set and verify Toobit's position-level SL/TP without clearing omitted legs."""
        toobit_sym = to_toobit_symbol(symbol)
        pos_side = _position_side_from_order_side(side)
        if pos_side is None:
            return {
                "success": False,
                "mode": "live" if not self.is_demo else "demo",
                "symbol": toobit_sym,
                "message": f"Invalid position side: {side}",
            }
        if stop_loss <= 0 and take_profit <= 0:
            return {
                "success": False,
                "mode": "live" if not self.is_demo else "demo",
                "symbol": toobit_sym,
                "side": pos_side,
                "message": "At least one positive stop-loss or take-profit is required.",
            }

        if self.is_demo:
            if toobit_sym not in self._demo_positions:
                return {
                    "success": False,
                    "mode": "demo",
                    "symbol": toobit_sym,
                    "side": pos_side,
                    "message": "No open demo position found for protection update.",
                }
            if stop_loss > 0:
                self._demo_positions[toobit_sym]["stopLoss"] = stop_loss
            if take_profit > 0:
                self._demo_positions[toobit_sym]["takeProfit1"] = take_profit
            return {
                "success": True,
                "mode": "demo",
                "symbol": toobit_sym,
                "side": pos_side,
                "stopLoss": stop_loss,
                "takeProfit": take_profit,
                "verified": True,
            }

        rules = await self.get_symbol_rules(toobit_sym)
        tick_size = float(self._contract_rules_cache.get(toobit_sym, {}).get("tick_size", 0.0))

        sl_str, effective_sl = _price_to_exchange_precision(stop_loss, tick_size)
        tp_str, effective_tp = _price_to_exchange_precision(take_profit, tick_size)

        params: Dict[str, Any] = {"symbol": toobit_sym, "side": pos_side}
        if sl_str:
            params["stopLoss"] = sl_str
        if tp_str:
            params["takeProfit"] = tp_str
        if stop_loss_size is not None and stop_loss_size > 0:
            params["slSize"] = str(stop_loss_size)
        if take_profit_size is not None and take_profit_size > 0:
            params["tpSize"] = str(take_profit_size)

        try:
            response = await self._send_signed_request(
                "POST",
                "/api/v1/futures/position/trading-stop",
                params,
            )
            if not _api_response_is_success(response) or not _response_matches_tpsl(
                response,
                pos_side,
                effective_sl,
                effective_tp,
            ):
                return {
                    "success": False,
                    "mode": "live",
                    "symbol": toobit_sym,
                    "side": pos_side,
                    "message": "Toobit did not confirm the requested TP/SL values.",
                    "data": response,
                }

            self._position_tpsl_cache[toobit_sym] = {
                "stopLoss": effective_sl,
                "takeProfit": effective_tp,
                "side": pos_side,
                "updatedAt": time.time(),
            }
            logger.info(
                "[TPSL] Confirmed exchange TP/SL for %s (%s): SL=%s, TP=%s",
                toobit_sym,
                pos_side,
                params.get("stopLoss", "unchanged"),
                params.get("takeProfit", "unchanged"),
            )
            return {
                "success": True,
                "mode": "live",
                "symbol": toobit_sym,
                "side": pos_side,
                "stopLoss": effective_sl,
                "takeProfit": effective_tp,
                "verified": True,
                "data": response,
            }
        except Exception as exc:
            logger.error(
                "[TPSL] Failed to configure exchange TP/SL for %s (%s): %s",
                toobit_sym,
                pos_side,
                exc,
            )
            return {
                "success": False,
                "mode": "live",
                "symbol": toobit_sym,
                "side": pos_side,
                "error": str(exc),
                "message": f"Failed to set TP/SL: {exc}",
            }

    async def create_plan_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "STOP_MARKET",
        quantity: float = 0.0,
        trigger_price: float = 0.0,
        reduce_only: bool = True,
        position_side: Optional[str] = None,
        take_profit_slot: str = "takeProfit1",
    ) -> Dict[str, Any]:
        """Create one verified reduce-only conditional order without replacing other legs."""
        toobit_sym = to_toobit_symbol(symbol)
        normalized_side = _position_side_from_order_side(position_side or side)
        if normalized_side is None:
            return {
                "success": False,
                "mode": "live" if not self.is_demo else "demo",
                "symbol": toobit_sym,
                "message": f"Invalid position side: {position_side or side}",
            }
        if quantity <= 0 or trigger_price <= 0:
            return {
                "success": False,
                "mode": "live" if not self.is_demo else "demo",
                "symbol": toobit_sym,
                "message": "A positive quantity and trigger price are required.",
            }

        if self.is_demo:
            position = self._demo_positions.get(toobit_sym)
            if position is None:
                return {
                    "success": False,
                    "mode": "demo",
                    "symbol": toobit_sym,
                    "message": "No open demo position found for protection order.",
                }
            slot = take_profit_slot if "PROFIT" in order_type.upper() else "stopLoss"
            position[slot] = trigger_price
            return {
                "success": True,
                "mode": "demo",
                "symbol": toobit_sym,
                "triggerPrice": trigger_price,
                "type": order_type,
                "positionSide": normalized_side,
            }

        rules = await self.get_symbol_rules(toobit_sym)
        tick_size = float(self._contract_rules_cache.get(toobit_sym, {}).get("tick_size", 0.0))
        trigger_text, effective_trigger = _price_to_exchange_precision(trigger_price, tick_size)
        if not trigger_text:
            return {
                "success": False,
                "mode": "live",
                "symbol": toobit_sym,
                "message": "Protection trigger price could not be represented.",
            }

        close_side = "SELL_CLOSE" if normalized_side == "LONG" else "BUY_CLOSE"
        order_params = {
            "symbol": toobit_sym,
            "side": close_side,
            "type": "STOP",
            "priceType": "MARKET",
            "quantity": str(quantity),
            "stopPrice": trigger_text,
            "triggerBy": "CONTRACT_PRICE",
            "newClientOrderId": f"laya_plan_{time.time_ns()}",
        }
        try:
            response = await self._send_signed_request(
                "POST",
                "/api/v1/futures/order",
                order_params,
            )
            if not _api_response_is_success(response):
                return {
                    "success": False,
                    "mode": "live",
                    "symbol": toobit_sym,
                    "message": _api_response_error(response),
                    "data": response,
                }
            order_id = _response_order_id(response)
            if order_id is None:
                return {
                    "success": False,
                    "mode": "live",
                    "symbol": toobit_sym,
                    "message": "Toobit accepted the request without a protection order ID.",
                    "data": response,
                }
            return {
                "success": True,
                "mode": "live",
                "symbol": toobit_sym,
                "positionSide": normalized_side,
                "orderId": order_id,
                "triggerPrice": effective_trigger,
                "data": response,
            }
        except Exception as exc:
            return {
                "success": False,
                "mode": "live",
                "symbol": toobit_sym,
                "message": f"Could not place protection order: {exc}",
            }

    async def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancel all resting and plan orders for a given symbol."""
        toobit_sym = to_toobit_symbol(symbol)
        if self.is_demo:
            if toobit_sym in self._demo_positions:
                self._demo_positions[toobit_sym]["stopLoss"] = 0.0
                self._demo_positions[toobit_sym]["takeProfit1"] = 0.0
                self._demo_positions[toobit_sym]["takeProfit2"] = 0.0
            return {"success": True, "mode": "demo", "symbol": toobit_sym, "message": "All orders cancelled"}

        params = {"symbol": toobit_sym}
        for ep in ["/api/v1/futures/allOpenOrders", "/api/v1/futures/batchOrders"]:
            try:
                res = await self._send_signed_request("DELETE", ep, params)
                return {"success": True, "mode": "live", "symbol": toobit_sym, "data": res}
            except Exception as e:
                continue

        return {
            "success": False,
            "mode": "live",
            "symbol": toobit_sym,
            "message": "Cancel orders could not be confirmed",
        }

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel a specific open order by ID."""
        toobit_sym = to_toobit_symbol(symbol)
        if self.is_demo:
            return {"success": True, "mode": "demo", "orderId": order_id}

        params = {"symbol": toobit_sym, "orderId": order_id}
        try:
            res = await self._send_signed_request("DELETE", "/api/v1/futures/order", params)
            return {"success": True, "mode": "live", "symbol": toobit_sym, "data": res}
        except Exception as e:
            logger.warning(f"Failed to cancel order {order_id}: {e}")

        return {"success": False, "mode": "live", "symbol": toobit_sym, "error": f"Failed to cancel order {order_id}"}

    async def close(self):
        await self._http_client.aclose()


def generate_position_suggestion(
    position: Optional[Dict[str, Any]],
    current_price: float,
    signal: Optional[Dict[str, Any]] = None,
    laya_decision: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Synthesize position status with Laya System-1 probabilistic intelligence
    to produce actionable trade management recommendations.
    """
    sig = signal or {}
    laya = laya_decision or {}
    action = str(
        sig.get("action")
        or laya.get("primary_action")
        or laya.get("position")
        or "NEUTRAL"
    ).upper()
    raw_confidence = sig.get("confidence_pct")
    if raw_confidence is None:
        raw_confidence = sig.get("confidence")
    if raw_confidence is None:
        raw_confidence = laya.get("confidence_prob", laya.get("confidence"))
    confidence = _confidence_percent(raw_confidence, laya.get("confidence_score"))
    probs = laya.get("probabilities", {})
    targets = sig.get("targets", {})

    p_strong_long = _probability_value(probs, "STRONG_LONG", "strong_long", default=0.2)
    p_scalp_long = _probability_value(probs, "SCALP_LONG", "scalp_long", default=0.2)
    p_bull = p_strong_long + p_scalp_long

    p_strong_short = _probability_value(probs, "STRONG_SHORT", "strong_short", default=0.2)
    p_scalp_short = _probability_value(probs, "SCALP_SHORT", "scalp_short", default=0.2)
    p_bear = p_strong_short + p_scalp_short

    # Helper formatter to prevent showing $0.000000
    def fmt_p(val: float) -> str:
        if not val or val <= 0:
            return "--"
        return f"${val:.6f}" if val < 0.1 else f"${val:.2f}"

    # Determine baseline price
    base_price = current_price
    if base_price <= 0:
        base_price = sig.get("entry_price") or 0.0

    # If user has no active position
    if not position:
        entry = sig.get("entry_price") or targets.get("entry", base_price)
        if entry <= 0:
            entry = base_price

        tp1 = sig.get("take_profit_1") or targets.get("take_profit_1", (entry * 1.015 if entry > 0 else 0.0))
        sl = sig.get("stop_loss") or targets.get("stop_loss", (entry * 0.985 if entry > 0 else 0.0))

        if "LONG" in action:
            return {
                "hasPosition": False,
                "alignment": "NONE",
                "action": "ENTER_SETUP",
                "badgeText": "BULLISH SETUP 🟢",
                "badgeClass": "badge-profit",
                "headline": f"Ready for Entry: Laya suggests {action}",
                "suggestion": f"No active position. Laya System-1 detects bullish confluence ({confidence}% confidence). Recommended setup: Buy near {fmt_p(entry)}, Target TP1 at {fmt_p(tp1)}, Protective SL at {fmt_p(sl)}. Use fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} margin to prevent compounding volatility drag.",
                "steps": [
                    f"Consider opening LONG near {fmt_p(entry)}",
                    f"Position Sizing: Use Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} margin (or max {int(DEFAULT_KELLY_FRACTION_PCT*100)}% equity shield)",
                    f"Set Target Take Profit 1 at {fmt_p(tp1)}",
                    f"Set Stop Loss at {fmt_p(sl)}"
                ],
                "canFlashClose": False,
                "recommendedMarginUsd": DEFAULT_POSITION_MARGIN_USD,
                "sizingRule": f"Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} Margin (Quarter-Kelly max {int(DEFAULT_KELLY_FRACTION_PCT*100)}%)",
                "recommendedSl": sl,
                "recommendedTp": tp1
            }
        elif "SHORT" in action:
            tp1_s = sig.get("take_profit_1") or targets.get("take_profit_1", (entry * 0.985 if entry > 0 else 0.0))
            sl_s = sig.get("stop_loss") or targets.get("stop_loss", (entry * 1.015 if entry > 0 else 0.0))
            return {
                "hasPosition": False,
                "alignment": "NONE",
                "action": "ENTER_SETUP",
                "badgeText": "BEARISH SETUP 🔴",
                "badgeClass": "badge-danger",
                "headline": f"Ready for Entry: Laya suggests {action}",
                "suggestion": f"No active position. Laya System-1 detects downward momentum ({confidence}% confidence). Recommended setup: Short near {fmt_p(entry)}, Target TP1 at {fmt_p(tp1_s)}, Protective SL at {fmt_p(sl_s)}. Use fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} margin to prevent compounding volatility drag.",
                "steps": [
                    f"Consider opening SHORT near {fmt_p(entry)}",
                    f"Position Sizing: Use Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} margin (or max {int(DEFAULT_KELLY_FRACTION_PCT*100)}% equity shield)",
                    f"Set Target Take Profit 1 at {fmt_p(tp1_s)}",
                    f"Set Stop Loss at {fmt_p(sl_s)}"
                ],
                "canFlashClose": False,
                "recommendedMarginUsd": DEFAULT_POSITION_MARGIN_USD,
                "sizingRule": f"Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} Margin (Quarter-Kelly max {int(DEFAULT_KELLY_FRACTION_PCT*100)}%)",
                "recommendedSl": sl_s,
                "recommendedTp": tp1_s
            }
        else:
            return {
                "hasPosition": False,
                "alignment": "NONE",
                "action": "WAIT",
                "badgeText": "WAIT FOR SETUP ⚪",
                "badgeClass": "badge-neutral",
                "headline": "Market Consolidating: Stand By",
                "suggestion": "No active position. Market is currently consolidating without high directional conviction. Wait for Laya to identify a high-probability breakout.",
                "steps": [
                    "Keep powder dry and avoid over-trading",
                    "Wait for next candle close to confirm direction",
                    "Watch key support and resistance boundaries"
                ],
                "canFlashClose": False,
                "recommendedSl": 0.0,
                "recommendedTp": 0.0
            }

    # Position is active
    pos_side = position.get("side", "LONG").upper()
    entry = position.get("entryPrice", current_price)
    pnl = position.get("unrealizedPnl", 0.0)
    pnl_pct = position.get("pnlPercentage", 0.0)
    qty = position.get("quantity", 0.0)
    symbol = position.get("displaySymbol", position.get("symbol", "contract"))

    # Case 1: Holding LONG
    if pos_side == "LONG":
        # Target TP1 or bullish continuation
        if p_bull >= 0.50 or "LONG" in action:
            if pnl_pct >= 8.0:
                rec_sl = max(entry, current_price * 0.992)
                return {
                    "hasPosition": True,
                    "alignment": "ALIGNED",
                    "action": "HOLD_TRAIL_SL",
                    "badgeText": "PROTECT PROFIT 🛡️",
                    "badgeClass": "badge-profit",
                    "headline": f"Great Trade (+{pnl_pct:.1f}%): Trail Stop Loss",
                    "suggestion": f"Your {symbol} LONG is up +{pnl_pct:.1f}% (${pnl:+.2f}). Laya AI confirms ongoing bullish pressure ({int(p_bull*100)}%). Recommend moving your Stop Loss to break-even or above entry (${rec_sl:.6f}) to lock in guaranteed profit while riding the trend.",
                    "steps": [
                        f"Move Stop Loss to ${rec_sl:.6f} (Risk-Free Mode)",
                        f"Next Take Profit target: ${targets.get('take_profit_1', current_price * 1.015):.6f}",
                        "Let winners run while protecting capital"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": rec_sl,
                    "recommendedTp": targets.get("take_profit_1", current_price * 1.02)
                }
            else:
                return {
                    "hasPosition": True,
                    "alignment": "ALIGNED",
                    "action": "HOLD_TRAIL_SL",
                    "badgeText": "KEEP HOLDING 🟢",
                    "badgeClass": "badge-profit",
                    "headline": "Bullish Trend Aligned: Hold Position",
                    "suggestion": f"Laya AI probabilities favor continued upward move ({int(p_bull*100)}% bullish). Keep holding your LONG position towards target ${targets.get('take_profit_1', entry * 1.015):.6f}.",
                    "steps": [
                        "Keep position open",
                        f"Ensure Stop Loss is active at ${targets.get('stop_loss', entry * 0.985):.6f}",
                        f"Watch for TP1 target near ${targets.get('take_profit_1', entry * 1.02):.6f}"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": targets.get("stop_loss", entry * 0.985),
                    "recommendedTp": targets.get("take_profit_1", entry * 1.02)
                }
        elif p_bear >= 0.50 or "SHORT" in action:
            # Bearish reversal threat
            if pnl > 0:
                return {
                    "hasPosition": True,
                    "alignment": "OPPOSED",
                    "action": "TAKE_PROFIT",
                    "badgeText": "TAKE PROFIT 🎯",
                    "badgeClass": "badge-warning",
                    "headline": "⚠️ Momentum Turning Bearish: Cash Out Now",
                    "suggestion": f"Warning: Laya AI detects strong downward shift ({int(p_bear*100)}% short pressure). Your LONG is currently profitable (+{pnl_pct:.1f}%). Strongly consider taking profit or clicking Flash Close before momentum reverses your gains.",
                    "steps": [
                        "Click 'Flash Close' to capture profits immediately",
                        "Or close 50% size and tighten SL to current price",
                        "Prepare for potential short entry if breakdown confirms"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": current_price * 0.998,
                    "recommendedTp": current_price
                }
            else:
                return {
                    "hasPosition": True,
                    "alignment": "OPPOSED",
                    "action": "CUT_LOSS",
                    "badgeText": "EXIT / REDUCE RISK 🚨",
                    "badgeClass": "badge-danger",
                    "headline": "🚨 Bearish Breakdown: Reduce Exposure",
                    "suggestion": f"Risk Alert: Price is declining ({pnl_pct:.1f}%) and Laya AI indicates sellers are in control ({int(p_bear*100)}% bearish). Exit position via Flash Close to avoid deeper drawdown or liquidation.",
                    "steps": [
                        "Click 'Flash Close' to limit loss",
                        f"Liquidation threshold is at ${position.get('liquidationPrice', 0.0):.6f}",
                        "Preserve margin for higher-conviction setup"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": current_price,
                    "recommendedTp": entry
                }
        else:
            return {
                "hasPosition": True,
                "alignment": "NEUTRAL",
                "action": "HOLD_TRAIL_SL",
                "badgeText": "MONITOR ⏳",
                "badgeClass": "badge-neutral",
                "headline": "Market Pausing: Maintain Guard",
                "suggestion": f"Price is in a consolidation range. Your LONG is at {pnl_pct:+.1f}%. Keep your protective Stop Loss at ${targets.get('stop_loss', entry * 0.985):.6f} and wait for volume to pick up.",
                "steps": [
                    "Hold position while price remains above support",
                    f"Stop loss safety boundary: ${targets.get('stop_loss', entry * 0.985):.6f}",
                    "Monitor for next trend impulse"
                ],
                "canFlashClose": True,
                "recommendedSl": targets.get("stop_loss", entry * 0.985),
                "recommendedTp": targets.get("take_profit_1", entry * 1.015)
            }

    # Case 2: Holding SHORT
    else:
        if p_bear >= 0.50 or "SHORT" in action:
            if pnl_pct >= 8.0:
                rec_sl = min(entry, current_price * 1.008)
                return {
                    "hasPosition": True,
                    "alignment": "ALIGNED",
                    "action": "HOLD_TRAIL_SL",
                    "badgeText": "PROTECT PROFIT 🛡️",
                    "badgeClass": "badge-profit",
                    "headline": f"Short Working Well (+{pnl_pct:.1f}%): Trail Stop Loss",
                    "suggestion": f"Your {symbol} SHORT is profitable (+{pnl_pct:.1f}% / ${pnl:+.2f}). Laya AI shows continued bearish follow-through ({int(p_bear*100)}%). Lower your Stop Loss to ${rec_sl:.6f} to lock in profit.",
                    "steps": [
                        f"Lower Stop Loss to ${rec_sl:.6f} (Risk-Free)",
                        f"Next Take Profit target: ${targets.get('take_profit_1', current_price * 0.985):.6f}",
                        "Let downward trend continue"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": rec_sl,
                    "recommendedTp": targets.get("take_profit_1", current_price * 0.98)
                }
            else:
                return {
                    "hasPosition": True,
                    "alignment": "ALIGNED",
                    "action": "HOLD_TRAIL_SL",
                    "badgeText": "KEEP HOLDING 🔴",
                    "badgeClass": "badge-profit",
                    "headline": "Bearish Trend Aligned: Hold Short",
                    "suggestion": f"Laya AI indicates strong seller dominance ({int(p_bear*100)}% bearish). Keep holding your SHORT position towards target ${targets.get('take_profit_1', entry * 0.985):.6f}.",
                    "steps": [
                        "Keep SHORT position open",
                        f"Ensure Stop Loss is active at ${targets.get('stop_loss', entry * 1.015):.6f}",
                        f"Watch for TP1 target near ${targets.get('take_profit_1', entry * 0.98):.6f}"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": targets.get("stop_loss", entry * 1.015),
                    "recommendedTp": targets.get("take_profit_1", entry * 0.98)
                }
        elif p_bull >= 0.50 or "LONG" in action:
            if pnl > 0:
                return {
                    "hasPosition": True,
                    "alignment": "OPPOSED",
                    "action": "TAKE_PROFIT",
                    "badgeText": "TAKE PROFIT 🎯",
                    "badgeClass": "badge-warning",
                    "headline": "⚠️ Bullish Bounce Detected: Close Short",
                    "suggestion": f"Warning: Buyers are stepping in ({int(p_bull*100)}% bull probability). Your SHORT is in profit (+{pnl_pct:.1f}%). Cash out now using Flash Close to secure your gains.",
                    "steps": [
                        "Click 'Flash Close' to capture profits",
                        "Avoid holding short into strong bounce",
                        "Look for clean re-entry or wait"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": current_price * 1.002,
                    "recommendedTp": current_price
                }
            else:
                return {
                    "hasPosition": True,
                    "alignment": "OPPOSED",
                    "action": "CUT_LOSS",
                    "badgeText": "EXIT / CUT LOSS 🚨",
                    "badgeClass": "badge-danger",
                    "headline": "🚨 Bullish Squeeze Risk: Exit Short",
                    "suggestion": f"Risk Alert: Price is moving up against your SHORT ({pnl_pct:.1f}%). Laya AI signals bullish momentum ({int(p_bull*100)}%). Flash close now to prevent short-squeeze liquidation.",
                    "steps": [
                        "Click 'Flash Close' immediately",
                        f"Liquidation threshold is at ${position.get('liquidationPrice', 0.0):.6f}",
                        "Protect account equity"
                    ],
                    "canFlashClose": True,
                    "recommendedSl": current_price,
                    "recommendedTp": entry
                }
        else:
            return {
                "hasPosition": True,
                "alignment": "NEUTRAL",
                "action": "HOLD_TRAIL_SL",
                "badgeText": "MONITOR ⏳",
                "badgeClass": "badge-neutral",
                "headline": "Market Sideways: Keep Alert",
                "suggestion": f"Price is stalling. Your SHORT is at {pnl_pct:+.1f}%. Keep your protective Stop Loss at ${targets.get('stop_loss', entry * 1.015):.6f}.",
                "steps": [
                    "Hold position while price stays below resistance",
                    f"Stop loss safety boundary: ${targets.get('stop_loss', entry * 1.015):.6f}",
                    "Wait for seller volume"
                ],
                "canFlashClose": True,
                "recommendedSl": targets.get("stop_loss", entry * 1.015),
                "recommendedTp": targets.get("take_profit_1", entry * 0.985)
            }

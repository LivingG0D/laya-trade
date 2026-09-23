import asyncio
import hashlib
import hmac
import logging
import time
from typing import Dict, Any, List, Optional
import httpx

from .config import TOOBIT_REST_URL, DEFAULT_SYMBOL
from .market_data import to_toobit_symbol

logger = logging.getLogger("toobit_client")


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
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10, keepalive_expiry=30.0)
        )

        # Demo mode state storage
        self._demo_balance = 5000.0  # $5,000 USDT virtual starting margin
        self._demo_positions: Dict[str, Dict[str, Any]] = {}
        
        # If demo mode is default or requested without keys, initialize a default demo position for 1000PEPE
        if is_demo:
            self._init_default_demo_position()

    def _init_default_demo_position(self):
        """Seed an initial demo position for 1000PEPE so users can test immediately."""
        toobit_sym = to_toobit_symbol(DEFAULT_SYMBOL)
        self._demo_positions[toobit_sym] = {
            "symbol": toobit_sym,
            "displaySymbol": DEFAULT_SYMBOL,
            "side": "LONG",
            "positionSide": "LONG",
            "quantity": 100000.0,
            "entryPrice": 0.004180,
            "markPrice": 0.004245,
            "leverage": 10,
            "margin": 41.80,
            "unrealizedPnl": 6.50,
            "pnlPercentage": 15.55,
            "liquidationPrice": 0.003780,
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
        logger.info("ToobitClient disconnected and session cleared")

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA-256 signature from sorted query parameters."""
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        query_string = "&".join(f"{k}={v}" for k, v in sorted_items)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _send_signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send a signed HTTP request to Toobit API."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Toobit API Key and Secret are required for live operations.")

        req_params = dict(params or {})
        req_params["timestamp"] = int(time.time() * 1000)
        req_params["recvWindow"] = 50000

        sig = self._generate_signature(req_params)
        req_params["signature"] = sig

        headers = {
            "X-BB-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}{endpoint}"

        if method.upper() == "GET":
            resp = await self._http_client.get(url, params=req_params, headers=headers)
        elif method.upper() == "POST":
            # Toobit accept signed params in query string or json body
            resp = await self._http_client.post(url, params=req_params, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        if resp.status_code != 200:
            logger.error(f"Toobit API error [{resp.status_code}]: {resp.text}")
            try:
                err_data = resp.json()
                err_msg = err_data.get("msg") or err_data.get("message") or resp.text
            except Exception:
                err_msg = resp.text
            raise RuntimeError(f"Toobit API Error ({resp.status_code}): {err_msg}")

        return resp.json()

    async def test_connection(self) -> Dict[str, Any]:
        """Test API credentials by fetching balance."""
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

        try:
            balance_data = await self._send_signed_request("GET", "/api/v1/futures/balance")
            return {
                "success": True,
                "mode": "live",
                "message": "Successfully authenticated with Toobit Futures API!",
                "data": balance_data
            }
        except Exception as e:
            logger.warning(f"Toobit connection test failed: {e}")
            return {
                "success": False,
                "mode": "error",
                "message": str(e)
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
            # Parse Toobit balance format (usually array of asset balances or dict)
            usdt_bal = {}
            if isinstance(raw_balances, list):
                for b in raw_balances:
                    if b.get("asset") == "USDT":
                        usdt_bal = b
                        break
            elif isinstance(raw_balances, dict):
                usdt_bal = raw_balances

            return {
                "mode": "live",
                "asset": "USDT",
                "totalBalance": float(usdt_bal.get("balance", usdt_bal.get("totalWalletBalance", 0.0))),
                "availableBalance": float(usdt_bal.get("availableBalance", usdt_bal.get("availableMargin", 0.0))),
                "marginBalance": float(usdt_bal.get("positionMargin", 0.0)),
                "unrealizedPnl": float(usdt_bal.get("unrealizedProfit", 0.0)),
                "totalEquity": float(usdt_bal.get("equity", 0.0))
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
            params = {}
            if symbol:
                params["symbol"] = to_toobit_symbol(symbol)

            raw_positions = await self._send_signed_request("GET", "/api/v1/futures/positions", params)
            active_positions = []

            if isinstance(raw_positions, list):
                for p in raw_positions:
                    qty = float(p.get("quantity", p.get("positionAmt", p.get("size", 0.0))))
                    if abs(qty) > 0:
                        side = p.get("positionSide") or ("LONG" if qty > 0 else "SHORT")
                        entry_price = float(p.get("entryPrice", 0.0))
                        mark_price = float(p.get("markPrice", current_market_price or entry_price))
                        unrealized_pnl = float(p.get("unrealizedPnl", p.get("unRealizedProfit", 0.0)))
                        leverage = int(p.get("leverage", 10))
                        liq_price = float(p.get("liquidationPrice", 0.0))
                        margin = float(p.get("margin", (abs(qty) * entry_price) / max(leverage, 1)))

                        pnl_pct = 0.0
                        if margin > 0:
                            pnl_pct = (unrealized_pnl / margin) * 100

                        active_positions.append({
                            "symbol": p.get("symbol"),
                            "displaySymbol": symbol or p.get("symbol"),
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
                            "isolated": bool(p.get("isolated", True))
                        })

            return active_positions
        except Exception as e:
            logger.error(f"Error fetching Toobit positions: {e}")
            return []

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
            params = {
                "symbol": toobit_sym
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
        leverage: int = 10
    ) -> Dict[str, Any]:
        """Open or replace a simulated demo position for testing."""
        toobit_sym = to_toobit_symbol(symbol)
        side_norm = side.upper()
        if side_norm not in ("LONG", "SHORT"):
            side_norm = "LONG"

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
            "isolated": True,
            "createdAt": int(time.time() * 1000)
        }
        self._demo_positions[toobit_sym] = pos
        return pos

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
    action = sig.get("action", laya.get("primary_action", "NEUTRAL")).upper()
    confidence = sig.get("confidence_pct", sig.get("confidence", laya.get("confidence", 50)))
    probs = laya.get("probabilities", {})
    targets = sig.get("targets", {})

    p_strong_long = probs.get("strong_long", 0.2)
    p_scalp_long = probs.get("scalp_long", 0.2)
    p_bull = p_strong_long + p_scalp_long

    p_strong_short = probs.get("strong_short", 0.2)
    p_scalp_short = probs.get("scalp_short", 0.2)
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
                "suggestion": f"No active position. Laya System-1 detects bullish confluence ({confidence}% confidence). Recommended setup: Buy near {fmt_p(entry)}, Target TP1 at {fmt_p(tp1)}, Protective SL at {fmt_p(sl)}.",
                "steps": [
                    f"Consider opening LONG near {fmt_p(entry)}",
                    f"Set Target Take Profit 1 at {fmt_p(tp1)}",
                    f"Set Stop Loss at {fmt_p(sl)}"
                ],
                "canFlashClose": False,
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
                "suggestion": f"No active position. Laya System-1 detects downward momentum ({confidence}% confidence). Recommended setup: Short near {fmt_p(entry)}, Target TP1 at {fmt_p(tp1_s)}, Protective SL at {fmt_p(sl_s)}.",
                "steps": [
                    f"Consider opening SHORT near {fmt_p(entry)}",
                    f"Set Target Take Profit 1 at {fmt_p(tp1_s)}",
                    f"Set Stop Loss at {fmt_p(sl_s)}"
                ],
                "canFlashClose": False,
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

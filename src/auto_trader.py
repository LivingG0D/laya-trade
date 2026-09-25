"""
Autonomous Algorithmic Trading Supervisor Engine for Laya Trade Analyzer.
Integrates Laya System-1 probabilistic intelligence with Toobit Futures execution,
zero-rejection position sizing, automatic TP/SL brackets, break-even profit locking,
opposing signal reversal exits, and strict portfolio circuit breakers.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import (
    DEFAULT_SYMBOL,
    DEFAULT_LEVERAGE,
    LEVERAGE_MAX_SL_PCT,
    LEVERAGE_MIN_SL_PCT,
    BASE_DIR
)
from .market_data import to_toobit_symbol

logger = logging.getLogger("auto_trader")

STATE_FILE = BASE_DIR / "scratch" / "autotrade_state.json"


class AutoTrader:
    """
    Autonomous Execution & Safety Supervisor Engine.
    Runs continuous background lifecycle management over trades and market conditions.
    """

    def __init__(
        self,
        toobit_client,
        market_service,
        laya_engine,
        signal_generator,
        ws_manager=None
    ):
        self.toobit_client = toobit_client
        self.market_service = market_service
        self.laya_engine = laya_engine
        self.signal_generator = signal_generator
        self.ws_manager = ws_manager

        self.enabled: bool = False
        self.status: str = "IDLE"  # IDLE, SCANNING, IN_POSITION, COOLDOWN, HALTED
        self.status_message: str = "Auto-trading standby (Disabled)"

        # Strategy & Risk Configuration
        self.settings: Dict[str, Any] = {
            "mode": "demo",  # "demo" or "live"
            "leverage": DEFAULT_LEVERAGE,  # 50x
            "sizing_mode": "fixed_margin",  # "fixed_margin" or "pct_equity"
            "fixed_margin_usd": 1.0,  # $1.00 USDT safe default
            "equity_risk_pct": 1.0,  # 1.0% equity
            "min_confidence": 65.0,  # 65% minimum confidence
            "max_positions": 1,  # 1 position maximum to prevent overexposure
            "auto_bracket": True,  # Always attach protective Stop-Loss & Take-Profit
            "auto_breakeven": True,  # Move SL to Entry at TP1 (+0.45R)
            "opposing_reversal_exit": True,  # Flash close if opposite high-conviction signal emerges
            "max_daily_loss_usd": 5.0,  # Circuit breaker: halt if daily loss reaches $5.00
            "max_consecutive_losses": 3,  # Circuit breaker: halt after 3 losses
            "cooldown_seconds": 30,  # Wait 30s after closing before next trade
        }

        # Real-time Metrics & Circuit Breaker Tracking
        self.stats: Dict[str, Any] = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_realized_pnl": 0.0,
            "daily_realized_pnl": 0.0,
            "consecutive_losses": 0,
            "last_trade_time": 0.0,
            "cooldown_until": 0.0,
            "current_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        # Active Trade Management State
        self._active_trade: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self._is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._last_scan_time: float = 0.0

        # Audit Logs (rolling list of 100 entries)
        self.logs: List[Dict[str, Any]] = []

        # Load persisted settings and state
        self._load_state()

        self.add_log(
            "info",
            "SYSTEM",
            f"AutoTrader initialized. Mode: {self.settings['mode'].upper()} | Leverage: {self.settings['leverage']}x | Status: IDLE"
        )

    # -------------------------------------------------------------------------
    # State Persistence
    # -------------------------------------------------------------------------

    def _load_state(self):
        """Restore persisted settings and daily stats if available."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                saved_settings = data.get("settings", {})
                for k, v in saved_settings.items():
                    if k in self.settings:
                        self.settings[k] = v

                saved_stats = data.get("stats", {})
                # Reset daily PnL if new UTC day
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if saved_stats.get("current_day") == today:
                    self.stats["daily_realized_pnl"] = float(saved_stats.get("daily_realized_pnl", 0.0))
                    self.stats["consecutive_losses"] = int(saved_stats.get("consecutive_losses", 0))
                else:
                    self.stats["current_day"] = today
                    self.stats["daily_realized_pnl"] = 0.0
                    self.stats["consecutive_losses"] = 0

                self.stats["total_trades"] = int(saved_stats.get("total_trades", 0))
                self.stats["winning_trades"] = int(saved_stats.get("winning_trades", 0))
                self.stats["losing_trades"] = int(saved_stats.get("losing_trades", 0))
                self.stats["total_realized_pnl"] = float(saved_stats.get("total_realized_pnl", 0.0))
        except Exception as e:
            logger.warning(f"Could not load AutoTrader state: {e}")

    def _save_state(self):
        """Persist settings and stats to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "settings": self.settings,
                "stats": self.stats,
                "updated_at": time.time()
            }
            STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save AutoTrader state: {e}")

    # -------------------------------------------------------------------------
    # Audit Logging & Client Broadcasts
    # -------------------------------------------------------------------------

    def add_log(self, level: str, category: str, message: str):
        """Record timestamped audit log entry and broadcast to UI."""
        now = time.time()
        time_str = datetime.now().strftime("%H:%M:%S")
        entry = {
            "id": f"log_{int(now * 1000)}_{len(self.logs)}",
            "time": time_str,
            "timestamp": now,
            "level": level,  # info, success, warning, danger
            "category": category,  # SYSTEM, SCAN, ORDER, BRACKET, PROTECTION, CIRCUIT_BREAKER
            "message": message
        }
        self.logs.insert(0, entry)
        if len(self.logs) > 120:
            self.logs = self.logs[:100]

        logger.info(f"[{category}] {message}")

        if self.ws_manager:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self.ws_manager.broadcast({
                        "type": "autotrade_log",
                        "log": entry,
                        "status": self.get_status()
                    }))
            except RuntimeError:
                pass

    # -------------------------------------------------------------------------
    # Status & Configuration APIs
    # -------------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive AutoTrader state payload for UI."""
        win_rate = 0.0
        if self.stats["total_trades"] > 0:
            win_rate = round((self.stats["winning_trades"] / self.stats["total_trades"]) * 100, 1)

        cooldown_remaining = max(0, int(self.stats["cooldown_until"] - time.time()))

        return {
            "enabled": self.enabled,
            "mode": self.settings["mode"],
            "status": self.status,
            "status_message": self.status_message,
            "settings": dict(self.settings),
            "stats": {
                **self.stats,
                "win_rate": win_rate,
                "cooldown_remaining": cooldown_remaining,
            },
            "active_trade": self._active_trade,
            "logs": self.logs[:30]  # latest 30 logs for snappy frontend rendering
        }

    def _mode_mismatch_reason(self) -> Optional[str]:
        """Return a safety reason when configured and exchange modes differ."""
        expected_demo = self.settings.get("mode") == "demo"
        client_is_demo = bool(getattr(self.toobit_client, "is_demo", False))
        if expected_demo != client_is_demo:
            expected_mode = "DEMO" if expected_demo else "LIVE"
            actual_mode = "DEMO" if client_is_demo else "LIVE"
            return f"Configured {expected_mode} mode does not match the connected {actual_mode} client."
        if not expected_demo and not getattr(self.toobit_client, "api_key", ""):
            return "Live mode requires a connected Toobit API key."
        return None

    def _halt_for_mode_mismatch(self, reason: str) -> Dict[str, Any]:
        """Persist a hard stop whenever execution-mode state is ambiguous."""
        self.enabled = False
        self.status = "HALTED"
        self.status_message = f"HALTED: {reason}"
        self.add_log("danger", "CIRCUIT_BREAKER", f"Execution mode mismatch: {reason}")
        self._save_state()
        return {"success": False, "enabled": False, "message": self.status_message, "status": self.get_status()}

    async def toggle(self, enable: bool) -> Dict[str, Any]:
        """Enable or disable auto-trading safely."""
        async with self._lock:
            # If turning ON, reset circuit breaker halts if daily limits are safe
            if enable:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self.stats["current_day"] != today:
                    self.stats["current_day"] = today
                    self.stats["daily_realized_pnl"] = 0.0
                    self.stats["consecutive_losses"] = 0

                # Validate daily loss limit before enabling
                if self.stats["daily_realized_pnl"] <= -abs(self.settings["max_daily_loss_usd"]):
                    msg = f"Cannot enable AutoTrader: Daily loss cap (-${self.settings['max_daily_loss_usd']:.2f}) was reached today."
                    self.add_log("danger", "CIRCUIT_BREAKER", msg)
                    return {"success": False, "enabled": False, "message": msg}

                mode_error = self._mode_mismatch_reason()
                if mode_error:
                    return self._halt_for_mode_mismatch(mode_error)

                self.enabled = True
                self.status = "SCANNING"
                self.status_message = "Auto-trading armed: Monitoring market for high-conviction setups."
                self.add_log("success", "SYSTEM", f"🟢 Auto-trading ENABLED in {self.settings['mode'].upper()} mode. 50x Risk Engine active.")
            else:
                self.enabled = False
                self.status = "IDLE"
                self.status_message = "Auto-trading disabled by trader."
                self.add_log("info", "SYSTEM", "⚪ Auto-trading DISABLED. Standing by.")

            self._save_state()
            return {"success": True, "enabled": self.enabled, "status": self.get_status()}

    async def update_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Update strategy and risk parameters with strict safety boundaries."""
        async with self._lock:
            # Sizing mode & margin
            if "sizing_mode" in new_settings:
                mode = str(new_settings["sizing_mode"]).lower()
                if mode in ("fixed_margin", "pct_equity"):
                    self.settings["sizing_mode"] = mode

            if "fixed_margin_usd" in new_settings:
                val = float(new_settings["fixed_margin_usd"])
                # Safe limits: $0.25 to $50.00
                self.settings["fixed_margin_usd"] = max(0.25, min(50.0, val))

            if "equity_risk_pct" in new_settings:
                val = float(new_settings["equity_risk_pct"])
                # Safe limits: 0.25% to 5.0%
                self.settings["equity_risk_pct"] = max(0.25, min(5.0, val))

            if "leverage" in new_settings:
                val = int(new_settings["leverage"])
                # Safe leverage boundary: 5x to 50x
                self.settings["leverage"] = max(5, min(50, val))

            if "min_confidence" in new_settings:
                val = float(new_settings["min_confidence"])
                # Confidence gate: 55% to 85%
                self.settings["min_confidence"] = max(55.0, min(85.0, val))

            if "mode" in new_settings:
                m = str(new_settings["mode"]).lower()
                if m in ("demo", "live"):
                    self.settings["mode"] = m

            if self.enabled:
                mode_error = self._mode_mismatch_reason()
                if mode_error:
                    return self._halt_for_mode_mismatch(mode_error)

            for bool_key in ("auto_bracket", "auto_breakeven", "opposing_reversal_exit"):
                if bool_key in new_settings:
                    self.settings[bool_key] = bool(new_settings[bool_key])

            if "max_daily_loss_usd" in new_settings:
                self.settings["max_daily_loss_usd"] = max(1.0, float(new_settings["max_daily_loss_usd"]))

            if "max_consecutive_losses" in new_settings:
                self.settings["max_consecutive_losses"] = max(2, int(new_settings["max_consecutive_losses"]))

            if "cooldown_seconds" in new_settings:
                self.settings["cooldown_seconds"] = max(10, int(new_settings["cooldown_seconds"]))

            self._save_state()
            self.add_log(
                "info",
                "SYSTEM",
                f"Settings updated: Mode={self.settings['mode'].upper()} | Lev={self.settings['leverage']}x | Sizing={self.settings['sizing_mode']} (${self.settings['fixed_margin_usd']:.2f}) | MinConf={self.settings['min_confidence']}%"
            )
            return {"success": True, "settings": self.settings, "status": self.get_status()}

    async def _cancel_emergency_orders(self, symbols: List[str]) -> List[str]:
        """Cancel orders for each known symbol and report every failure."""
        errors: List[str] = []
        for symbol in sorted(set(symbols)):
            try:
                result = await self.toobit_client.cancel_all_orders(symbol)
                if not result or not result.get("success"):
                    message = (result or {}).get("message", "cancellation was not confirmed")
                    errors.append(f"{symbol}: {message}")
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        return errors

    async def _close_emergency_positions(self, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Request closes and count only explicitly successful responses."""
        closed_count = 0
        errors: List[str] = []
        for position in positions:
            symbol = position.get("rawSymbol") or position.get("symbol")
            if not symbol:
                errors.append("position missing symbol")
                continue
            try:
                result = await self.toobit_client.flash_close(symbol)
                if result and result.get("success"):
                    closed_count += 1
                else:
                    errors.append(f"{symbol}: {(result or {}).get('message', 'close was not confirmed')}")
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        return {"closed_count": closed_count, "errors": errors}

    async def emergency_stop(self) -> Dict[str, Any]:
        """
        Emergency Kill Switch: Instantly halt trading, cancel all resting orders,
        and flash-close all active positions across the account.
        """
        async with self._lock:
            self.enabled = False
            self.status = "HALTED"
            self.status_message = "EMERGENCY KILL SWITCH ACTIVATED: All auto-trading stopped."
            self.add_log(
                "danger",
                "CIRCUIT_BREAKER",
                "🚨 EMERGENCY KILL SWITCH triggered! Disabling auto-trader, cancelling orders, and closing all open positions!"
            )

            errors: List[str] = []
            open_positions: List[Dict[str, Any]] = []
            try:
                open_positions = await self.toobit_client.get_positions(symbol=None)
            except Exception as exc:
                errors.append(f"position query: {exc}")

            current_symbol = getattr(self.market_service, "current_symbol", None)
            symbols = [current_symbol] if current_symbol else []
            symbols.extend(
                position.get("rawSymbol") or position.get("symbol")
                for position in open_positions
                if position.get("rawSymbol") or position.get("symbol")
            )
            errors.extend(await self._cancel_emergency_orders(symbols))

            close_result = await self._close_emergency_positions(open_positions)
            closed_count = close_result["closed_count"]
            errors.extend(close_result["errors"])

            try:
                remaining_positions = await self.toobit_client.get_positions(symbol=None)
            except Exception as exc:
                remaining_positions = []
                errors.append(f"final position verification: {exc}")

            if remaining_positions:
                errors.append(f"{len(remaining_positions)} position(s) remain after close requests")

            success = not errors
            if success:
                self._active_trade = None
                self.status_message = f"Emergency stop complete: {closed_count} position(s) closed."
                log_level = "warning"
            else:
                self.status_message = "Emergency stop incomplete: manual verification is required."
                log_level = "danger"

            self._save_state()
            self.add_log(
                log_level,
                "CIRCUIT_BREAKER",
                f"Emergency stop result: {self.status_message} Errors: {'; '.join(errors) if errors else 'none'}",
            )

            return {
                "success": success,
                "closed_count": closed_count,
                "errors": errors,
                "message": self.status_message,
                "status": self.get_status(),
            }

    def clear_logs(self) -> Dict[str, Any]:
        """Clear audit logs."""
        self.logs.clear()
        self.add_log("info", "SYSTEM", "Audit logs cleared by trader.")
        return {"success": True, "logs": self.logs}

    # -------------------------------------------------------------------------
    # Core Autonomous Execution & Supervisor Loop
    # -------------------------------------------------------------------------

    def start_loop(self):
        """Start background supervisor loop if not already running."""
        if not self._is_running:
            self._is_running = True
            self._loop_task = asyncio.create_task(self._run_supervisor_loop())
            logger.info("AutoTrader background supervisor loop launched.")

    async def stop_loop(self):
        """Stop background supervisor loop."""
        self._is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("AutoTrader background loop stopped.")

    async def _run_supervisor_loop(self):
        """Continuous background loop running every 5 seconds."""
        while self._is_running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in AutoTrader supervisor loop: {e}", exc_info=True)
            await asyncio.sleep(5)

    async def tick(self):
        """Periodic evaluation of market conditions, active trades, and circuit breakers."""
        # 1. Date rollover check for daily loss counter
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.stats["current_day"] != today:
            self.stats["current_day"] = today
            self.stats["daily_realized_pnl"] = 0.0
            self.stats["consecutive_losses"] = 0
            self._save_state()

        # 2. Check if AutoTrader is enabled
        if not self.enabled:
            if self.status != "HALTED":
                self.status = "IDLE"
                self.status_message = "Auto-trading standby (Disabled)"
            return

        # Check client mode alignment before any account or order operation
        mode_error = self._mode_mismatch_reason()
        if mode_error:
            self._halt_for_mode_mismatch(mode_error)
            return

        # 3. Circuit Breaker Check: Daily Loss Limit
        max_daily_loss = abs(float(self.settings["max_daily_loss_usd"]))
        if self.stats["daily_realized_pnl"] <= -max_daily_loss:
            self.enabled = False
            self.status = "HALTED"
            self.status_message = f"HALTED: Daily loss limit (-${max_daily_loss:.2f}) reached today."
            self.add_log(
                "danger",
                "CIRCUIT_BREAKER",
                f"🛡️ Daily Loss Circuit Breaker Triggered: -${abs(self.stats['daily_realized_pnl']):.2f} lost today. Auto-trading shut down for capital safety."
            )
            self._save_state()
            return

        # 4. Circuit Breaker Check: Consecutive Losses Halt
        max_consec = int(self.settings["max_consecutive_losses"])
        if self.stats["consecutive_losses"] >= max_consec:
            self.enabled = False
            self.status = "HALTED"
            self.status_message = f"HALTED: {self.stats['consecutive_losses']} consecutive losses detected."
            self.add_log(
                "danger",
                "CIRCUIT_BREAKER",
                f"🛡️ Consecutive Loss Circuit Breaker: {max_consec} consecutive losses. Pausing trading for manual review."
            )
            self._save_state()
            return

        # 5. Fetch Open Positions across account
        target_sym = self.market_service.current_symbol
        open_positions = []
        try:
            open_positions = await self.toobit_client.get_positions(symbol=None)
        except Exception as e:
            logger.warning(f"AutoTrader could not query positions: {e}")
            return

        # ---------------------------------------------------------------------
        # STAGE A: Active Position Supervision
        # ---------------------------------------------------------------------
        if open_positions:
            pos = open_positions[0]
            pos_sym = pos.get("displaySymbol") or pos.get("symbol")
            pos_side = pos.get("side", "LONG").upper()
            pnl = float(pos.get("unrealizedPnl", 0.0))
            pnl_pct = float(pos.get("pnlPercentage", 0.0))
            entry_p = float(pos.get("entryPrice", 0.0))
            mark_p = float(pos.get("markPrice", entry_p))

            self.status = "IN_POSITION"
            self.status_message = f"Managing {pos_side} {pos_sym} ({pnl_pct:+.1f}% ROE, {'+' if pnl >= 0 else ''}${pnl:.2f})"

            # Sync active managed trade metadata
            if not self._active_trade or self._active_trade.get("symbol") != pos_sym:
                self._active_trade = {
                    "symbol": pos_sym,
                    "side": pos_side,
                    "entry_price": entry_p,
                    "quantity": float(pos.get("quantity", 0.0)),
                    "margin": float(pos.get("margin", 0.0)),
                    "stop_loss": float(pos.get("stopLoss", 0.0)),
                    "take_profit_1": float(pos.get("takeProfit1", pos.get("takeProfit", 0.0))),
                    "be_moved": False,
                    "open_time": time.time()
                }

            # Fetch current market intelligence for this coin
            analysis = None
            try:
                raw_klines = await self.market_service.fetch_klines(symbol=pos_sym, interval=self.market_service.current_interval, limit=100)
                from .indicators import compute_all_indicators
                calc = compute_all_indicators(raw_klines)
                latest = calc["latest"]
                latest["symbol"] = pos_sym
                latest["interval"] = self.market_service.current_interval
                dec = await self.laya_engine.decide(latest)
                analysis = self.signal_generator.generate_signal(latest, dec)
            except Exception:
                pass

            sig_action = analysis.get("action", "") if analysis else ""
            sig_conf = float(analysis.get("confidence_pct", 0.0)) if analysis else 0.0

            # Rule 0: Guarantee Active Position TP & SL on Exchange
            if self.settings.get("auto_bracket", True):
                curr_sl = float(self._active_trade.get("stop_loss", 0.0))
                curr_tp = float(self._active_trade.get("take_profit_1", 0.0))
                if curr_sl <= 0 or curr_tp <= 0:
                    calc_sl = curr_sl
                    calc_tp = curr_tp
                    if analysis and analysis.get("targets"):
                        t = analysis["targets"]
                        if calc_sl <= 0:
                            calc_sl = float(analysis.get("stop_loss") or t.get("stop_loss", 0.0))
                        if calc_tp <= 0:
                            calc_tp = float(analysis.get("take_profit_1") or t.get("take_profit_1", 0.0))

                    if calc_sl <= 0 or calc_tp <= 0:
                        lev = int(self.settings.get("leverage", 50))
                        # 0.5% stop distance (~25% ROE risk at 50x)
                        stop_dist_pct = 0.25 / lev
                        tp_dist_pct = 0.40 / lev
                        if pos_side == "LONG":
                            if calc_sl <= 0:
                                calc_sl = entry_p * (1.0 - stop_dist_pct)
                            if calc_tp <= 0:
                                calc_tp = entry_p * (1.0 + tp_dist_pct)
                        else:
                            if calc_sl <= 0:
                                calc_sl = entry_p * (1.0 + stop_dist_pct)
                            if calc_tp <= 0:
                                calc_tp = entry_p * (1.0 - tp_dist_pct)

                    self._active_trade["stop_loss"] = calc_sl
                    self._active_trade["take_profit_1"] = calc_tp
                    raw_sym = pos.get("rawSymbol") or pos.get("symbol")
                    try:
                        tpsl_res = await self.toobit_client.set_position_tpsl(
                            symbol=raw_sym,
                            side=pos_side,
                            stop_loss=calc_sl,
                            take_profit=calc_tp
                        )
                        if tpsl_res.get("success"):
                            self.add_log(
                                "success",
                                "BRACKET",
                                f"🎯 Exchange TP/SL Active: SL ${calc_sl:.2f} | TP ${calc_tp:.2f} armed directly on Toobit for {pos_side} {pos_sym}!"
                            )
                    except Exception as e:
                        logger.warning(f"Could not auto-arm position TP/SL: {e}")

            # Rule 1: Break-Even Profit Lock at TP1 or +9% ROE (+0.45R)
            if self.settings["auto_breakeven"] and not self._active_trade.get("be_moved"):
                tp1 = self._active_trade.get("take_profit_1", 0.0)
                be_condition = False
                be_sl_price = entry_p

                if pos_side == "LONG":
                    if pnl_pct >= 9.0 or (tp1 > 0 and mark_p >= tp1):
                        be_condition = True
                        be_sl_price = entry_p * 1.0005  # entry + 0.05% buffer for round-trip fees
                else:  # SHORT
                    if pnl_pct >= 9.0 or (tp1 > 0 and mark_p <= tp1):
                        be_condition = True
                        be_sl_price = entry_p * 0.9995

                if be_condition:
                    self._active_trade["be_moved"] = True
                    self.add_log(
                        "success",
                        "PROFIT_LOCK",
                        f"🛡️ Break-Even Profit Lock Triggered! {pos_side} {pos_sym} is up +{pnl_pct:.1f}% ROE. Moving Stop-Loss to Entry (${be_sl_price:.4f}) on Toobit to guarantee risk-free runner."
                    )
                    # Update exchange stop directly
                    raw_sym = pos.get("rawSymbol") or pos.get("symbol")
                    tp_val = self._active_trade.get("take_profit_1", 0.0)
                    try:
                        await self.toobit_client.set_position_tpsl(
                            symbol=raw_sym,
                            side=pos_side,
                            stop_loss=be_sl_price,
                            take_profit=tp_val
                        )
                    except Exception as e:
                        logger.warning(f"Could not update BE stop on exchange: {e}")

            # Rule 2: Opposing Signal Reversal Protection
            if self.settings["opposing_reversal_exit"] and sig_conf >= 70.0:
                opposed = False
                if pos_side == "LONG" and ("SHORT" in sig_action):
                    opposed = True
                elif pos_side == "SHORT" and ("LONG" in sig_action):
                    opposed = True

                if opposed:
                    self.add_log(
                        "warning",
                        "PROTECTION",
                        f"⚠️ Reversal Warning: Laya detected strong opposing momentum ({sig_action}, {sig_conf:.0f}% confidence). Executing Flash Close on {pos_side} {pos_sym} to protect capital!"
                    )
                    await self.toobit_client.flash_close(pos.get("rawSymbol") or pos.get("symbol"))
                    self._handle_trade_closed(pos, reason=f"Opposing {sig_action} ({sig_conf:.0f}% Conf)")
                    return

            # Rule 3: Simulated Demo SL/TP trigger check
            if self.settings["mode"] == "demo":
                sl = float(pos.get("stopLoss", 0.0))
                tp2 = float(pos.get("takeProfit2", 0.0))
                closed_reason = None

                if pos_side == "LONG":
                    if sl > 0 and mark_p <= sl:
                        closed_reason = f"Stop-Loss hit @ ${mark_p:.4f}"
                    elif tp2 > 0 and mark_p >= tp2:
                        closed_reason = f"Take-Profit 2 hit @ ${mark_p:.4f}"
                else:  # SHORT
                    if sl > 0 and mark_p >= sl:
                        closed_reason = f"Stop-Loss hit @ ${mark_p:.4f}"
                    elif tp2 > 0 and mark_p <= tp2:
                        closed_reason = f"Take-Profit 2 hit @ ${mark_p:.4f}"

                if closed_reason:
                    await self.toobit_client.flash_close(pos.get("rawSymbol") or pos.get("symbol"))
                    self._handle_trade_closed(pos, reason=closed_reason)
                    return

            return

        # ---------------------------------------------------------------------
        # STAGE B: No Position Active — Check Reconciliation & Cooldown
        # ---------------------------------------------------------------------
        if self._active_trade and not open_positions:
            self._handle_trade_closed(self._active_trade, reason="Exchange order filled / target reached")

        now = time.time()
        if now < self.stats["cooldown_until"]:
            remaining = int(self.stats["cooldown_until"] - now)
            self.status = "COOLDOWN"
            self.status_message = f"Cooldown active ({remaining}s remaining). Guarding against volatility whipsaws."
            return

        # ---------------------------------------------------------------------
        # STAGE C: Market Scan & Setup Evaluation
        # ---------------------------------------------------------------------
        self.status = "SCANNING"
        self.status_message = f"Scanning {target_sym} for high-conviction entries (min {self.settings['min_confidence']:.0f}% conf)..."

        # Throttle heavy inference checks to every 10 seconds
        if now - self._last_scan_time < 10.0:
            return
        self._last_scan_time = now

        # Fetch latest market data & generate signal
        try:
            tasks = [
                self.market_service.fetch_klines(
                    symbol=target_sym,
                    interval=self.market_service.current_interval,
                    limit=150
                )
            ]
            has_htf = hasattr(self.market_service, "fetch_htf_context")
            has_depth = hasattr(self.market_service, "fetch_order_book_depth")
            has_funding = hasattr(self.market_service, "fetch_funding_rate")

            if has_htf:
                tasks.append(self.market_service.fetch_htf_context(target_sym))
            if has_depth:
                tasks.append(self.market_service.fetch_order_book_depth(target_sym))
            if has_funding:
                tasks.append(self.market_service.fetch_funding_rate(target_sym))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            raw_klines = results[0]

            if isinstance(raw_klines, Exception) or not raw_klines:
                return

            from .indicators import compute_all_indicators
            calc = compute_all_indicators(raw_klines)
            latest = calc["latest"]
            latest["symbol"] = target_sym
            latest["interval"] = self.market_service.current_interval

            idx = 1
            if has_htf:
                htf_ctx = results[idx]
                if isinstance(htf_ctx, dict) and not isinstance(htf_ctx, Exception):
                    latest["htf"] = htf_ctx
                idx += 1
            if has_depth:
                depth_ctx = results[idx]
                if isinstance(depth_ctx, dict) and not isinstance(depth_ctx, Exception):
                    latest["depth"] = depth_ctx
                idx += 1
            if has_funding:
                funding_ctx = results[idx]
                if isinstance(funding_ctx, dict) and not isinstance(funding_ctx, Exception):
                    latest["funding"] = funding_ctx

            dec = await self.laya_engine.decide(latest)
            signal = self.signal_generator.generate_signal(latest, dec)
        except Exception as e:
            logger.warning(f"Error during AutoTrader scan on {target_sym}: {e}")
            return

        action = signal.get("action", "")
        confidence = float(signal.get("confidence_pct", 0.0))

        # Filter out neutral or low-confidence setups
        if action in ("NEUTRAL", "WAIT") or not action:
            return

        if confidence < float(self.settings["min_confidence"]):
            return

        if "LONG" not in action and "SHORT" not in action:
            return

        # ---------------------------------------------------------------------
        # STAGE D: Zero-Rejection Pre-Flight & Order Execution
        # ---------------------------------------------------------------------
        order_side = "BUY" if "LONG" in action else "SELL"
        pos_side = "LONG" if "LONG" in action else "SHORT"
        entry_price = float(signal.get("entry_price", latest.get("close", 0.0)))
        if entry_price <= 0:
            return

        # 1. Check account balance and available margin
        bal = await self.toobit_client.get_balance()
        avail_margin = float(bal.get("availableBalance", 0.0))
        total_equity = float(bal.get("totalEquity", bal.get("totalBalance", 0.0)))

        if avail_margin < 0.50:
            self.add_log(
                "warning",
                "ORDER",
                f"Pre-flight skipped for {target_sym}: Available margin (${avail_margin:.2f} USDT) is below minimum $0.50 required."
            )
            return

        # 2. Compute Target Margin Sizing
        if self.settings["sizing_mode"] == "pct_equity":
            target_margin = max(0.50, total_equity * (float(self.settings["equity_risk_pct"]) / 100.0))
        else:
            target_margin = max(0.50, float(self.settings["fixed_margin_usd"]))

        # 80% Free Margin Shield: Max 20% of available margin per trade
        max_safe_margin = avail_margin * 0.20
        if target_margin > max_safe_margin:
            target_margin = max_safe_margin

        lev = int(self.settings["leverage"])
        notional_value = target_margin * lev

        # 3. Quantize using symbol trading rules and contract multiplier
        rules = await self.toobit_client.get_symbol_rules(target_sym)
        multiplier = float(rules.get("multiplier", 1.0))
        step_size = float(rules.get("step_size", 0.001))
        min_qty = float(rules.get("min_qty", 0.001))
        min_notional = float(rules.get("min_notional", 5.0))

        # In Toobit Futures, order quantity is denominated in contracts
        # 1 contract value = entry_price * multiplier
        contract_unit_val = entry_price * multiplier
        if contract_unit_val <= 0:
            return

        raw_contract_qty = notional_value / contract_unit_val

        # Precision step quantization
        step_str = f"{step_size:.8f}".rstrip('0')
        decimals = len(step_str.split('.')[1]) if '.' in step_str else 0
        quantized_qty = round((raw_contract_qty // step_size) * step_size, decimals)

        if quantized_qty < min_qty:
            # Check if bumping to min_qty stays within safety buffer
            bump_margin = (min_qty * contract_unit_val) / lev
            if bump_margin <= (avail_margin * 0.40):
                quantized_qty = min_qty
            else:
                self.add_log(
                    "warning",
                    "ORDER",
                    f"Skipping {target_sym}: Exchange minQty ({min_qty} contracts) requires ${bump_margin:.2f} margin, exceeding safety cap."
                )
                return

        final_margin = (quantized_qty * contract_unit_val) / lev
        targets = signal.get("targets", {})
        sl_price = float(signal.get("stop_loss", targets.get("stop_loss", 0.0)))
        tp1_price = float(signal.get("take_profit_1", targets.get("take_profit_1", 0.0)))
        tp2_price = float(signal.get("take_profit_2", targets.get("take_profit_2", 0.0)))

        # 4. Dispatch Order
        async with self._lock:
            self.add_log(
                "info",
                "ORDER",
                f"🚀 Auto-Executing {lev}x {pos_side} on {target_sym}: Qty {quantized_qty} (~${final_margin:.2f} margin, {confidence:.0f}% confidence)..."
            )

            res = await self.toobit_client.create_order(
                symbol=target_sym,
                side=order_side,
                order_type="MARKET",
                quantity=quantized_qty,
                leverage=lev,
                entry_price=entry_price,
                stop_loss=sl_price,
                take_profit_1=tp1_price,
                take_profit_2=tp2_price
            )

            if not res.get("success"):
                err_msg = res.get("error") or res.get("message") or "Order placement rejected"
                self.add_log("danger", "ORDER", f"Order execution failed: {err_msg}")
                return

            self.stats["total_trades"] += 1
            self.stats["last_trade_time"] = time.time()

            self._active_trade = {
                "symbol": target_sym,
                "side": pos_side,
                "quantity": quantized_qty,
                "entry_price": entry_price,
                "margin": final_margin,
                "stop_loss": sl_price,
                "take_profit_1": tp1_price,
                "take_profit_2": tp2_price,
                "be_moved": False,
                "open_time": time.time()
            }

            self.status = "IN_POSITION"
            self.status_message = f"Opened {lev}x {pos_side} on {target_sym} @ ${entry_price:.4f}"
            self._save_state()

            self.add_log(
                "success",
                "ORDER",
                f"✅ Position Filled: {lev}x {pos_side} {quantized_qty} {target_sym} @ ${entry_price:.4f} (Margin: ${final_margin:.2f})!"
            )
            if self.settings["auto_bracket"]:
                self.add_log(
                    "info",
                    "BRACKET",
                    f"🎯 Protective Bracket Armed: SL ${sl_price:.4f} (-{abs(entry_price-sl_price)/entry_price*100:.2f}%) | TP1 ${tp1_price:.4f} | TP2 ${tp2_price:.4f}"
                )

    def _handle_trade_closed(self, trade_or_pos: Dict[str, Any], reason: str = ""):
        """Record trade completion statistics, realized PnL, and initiate cooldown."""
        pnl = float(trade_or_pos.get("unrealizedPnl", trade_or_pos.get("realizedPnl", 0.0)))
        pnl_pct = float(trade_or_pos.get("pnlPercentage", 0.0))
        sym = trade_or_pos.get("symbol", "")
        side = trade_or_pos.get("side", "")

        self.stats["total_realized_pnl"] += pnl
        self.stats["daily_realized_pnl"] += pnl

        if pnl >= 0:
            self.stats["winning_trades"] += 1
            self.stats["consecutive_losses"] = 0
            lvl = "success"
            outcome = f"WIN (+${pnl:.2f}, +{pnl_pct:.1f}% ROE)"
        else:
            self.stats["losing_trades"] += 1
            self.stats["consecutive_losses"] += 1
            lvl = "warning"
            outcome = f"LOSS (-${abs(pnl):.2f}, {pnl_pct:.1f}% ROE)"

        cooldown = int(self.settings["cooldown_seconds"])
        self.stats["cooldown_until"] = time.time() + cooldown
        self.status = "COOLDOWN"
        self.status_message = f"Cooldown active ({cooldown}s remaining). Guarding against volatility whipsaws."
        self._active_trade = None
        self._save_state()

        self.add_log(
            lvl,
            "SYSTEM",
            f"Trade Finalized [{reason}]: {side} {sym} -> {outcome}. Cooldown active for {cooldown}s."
        )

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
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    DEFAULT_SYMBOL,
    DEFAULT_LEVERAGE,
    LEVERAGE_MAX_SL_PCT,
    LEVERAGE_MIN_SL_PCT,
    BASE_DIR,
    SUPPORTED_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
)
from .market_data import to_toobit_symbol

logger = logging.getLogger("auto_trader")

STATE_FILE = BASE_DIR / "scratch" / "autotrade_state.json"


def _position_key(symbol: str) -> str:
    """Normalize display and exchange symbol forms to one map key."""
    return to_toobit_symbol(str(symbol or "").strip())


def _timeframe_seconds(timeframe: str) -> int:
    """Return the nominal duration of a supported timeframe in seconds."""
    value = str(timeframe or "").lower()
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("d"):
        return int(value[:-1]) * 86400
    return 300


def _is_fresh_timestamp(timestamp: Any, timeframe: str) -> bool:
    """Allow a short clock skew while rejecting stale closed-candle data."""
    try:
        candle_time = float(timestamp)
    except (TypeError, ValueError):
        return False
    if candle_time <= 0:
        return False
    age = time.time() - candle_time
    return -60 <= age <= _timeframe_seconds(timeframe) * 2 + 30


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
            "symbols": ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"],
            "timeframes": ["5m", "15m", "1h"],
            "max_positions": 2,
            "scan_interval_seconds": 5,
            "signal_ttl_seconds": 15,
            "per_symbol_cooldown_seconds": 60,
            "max_concurrent_fetches": 6,
            "max_concurrent_decisions": 1,
            "shadow_mode": True,
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
        self._active_trades: Dict[str, Dict[str, Any]] = {}
        self._active_trade: Optional[Dict[str, Any]] = None
        self._analysis_results: Dict[str, Dict[str, Any]] = {}
        self._symbol_cooldowns: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._fetch_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_fetches"]))
        self._decision_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_decisions"]))
        self._is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._last_scan_time: float = 0.0

        # Audit Logs (rolling list of 100 entries)
        self.logs: List[Dict[str, Any]] = []

        # Load persisted settings and state
        self._load_state()
        self._fetch_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_fetches"]))
        self._decision_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_decisions"]))

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

                for trade in data.get("active_trades", []):
                    symbol = trade.get("symbol")
                    if symbol:
                        self._active_trades[_position_key(symbol)] = dict(trade)
                self._active_trade = next(iter(self._active_trades.values()), None)
        except Exception as e:
            logger.warning(f"Could not load AutoTrader state: {e}")

    def _save_state(self):
        """Persist settings and stats to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "settings": self.settings,
                "stats": self.stats,
                "active_trades": list(self._active_trades.values()),
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
            "active_trades": list(self._active_trades.values()),
            "analysis": dict(self._analysis_results),
            "logs": self.logs[:30]  # latest 30 logs for snappy frontend rendering
        }

    def get_active_trade(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return managed trade metadata for a display or exchange symbol."""
        return self._active_trades.get(_position_key(symbol))

    def get_active_trades(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all managed trades."""
        return list(self._active_trades.values())

    def _set_active_trade(self, trade: Optional[Dict[str, Any]]) -> None:
        """Synchronize the legacy primary trade view with the multi-trade map."""
        if trade is None:
            self._active_trade = None
            return
        symbol = trade.get("symbol")
        if not symbol:
            return
        self._active_trades[_position_key(symbol)] = trade
        self._active_trade = trade

    def _remove_active_trade(self, symbol: str) -> None:
        """Remove one managed trade without affecting other symbols."""
        self._active_trades.pop(_position_key(symbol), None)
        self._active_trade = next(iter(self._active_trades.values()), None)

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
                if self.settings.get("shadow_mode", True):
                    self.status_message = "Auto-trading armed in SHADOW mode: no orders will be submitted."
                    self.add_log("success", "SYSTEM", f"🟢 Auto-trading ENABLED in {self.settings['mode'].upper()} SHADOW mode.")
                else:
                    self.status_message = "Auto-trading armed: Monitoring configured symbols for high-conviction setups."
                    self.add_log("success", "SYSTEM", f"🟢 Auto-trading ENABLED in {self.settings['mode'].upper()} mode.")
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

            if "symbols" in new_settings:
                raw_symbols = new_settings["symbols"]
                if isinstance(raw_symbols, str):
                    raw_symbols = [raw_symbols]
                allowed = {to_toobit_symbol(str(symbol)) for symbol in SUPPORTED_SYMBOLS}
                normalized_symbols = []
                seen_symbols = set()
                for symbol in raw_symbols if isinstance(raw_symbols, list) else []:
                    normalized = to_toobit_symbol(str(symbol))
                    display_symbol = str(symbol).upper()
                    if normalized in allowed and normalized not in seen_symbols:
                        seen_symbols.add(normalized)
                        normalized_symbols.append(display_symbol)
                if normalized_symbols:
                    self.settings["symbols"] = normalized_symbols[:10]

            if "timeframes" in new_settings:
                raw_timeframes = new_settings["timeframes"]
                if isinstance(raw_timeframes, str):
                    raw_timeframes = [raw_timeframes]
                normalized_timeframes = [
                    str(timeframe).lower()
                    for timeframe in raw_timeframes if isinstance(raw_timeframes, list)
                    if str(timeframe).lower() in SUPPORTED_TIMEFRAMES
                ]
                if normalized_timeframes:
                    self.settings["timeframes"] = list(dict.fromkeys(normalized_timeframes))

            if "max_positions" in new_settings:
                self.settings["max_positions"] = max(1, min(2, int(new_settings["max_positions"])))

            for key, minimum, maximum in (
                ("scan_interval_seconds", 2, 60),
                ("signal_ttl_seconds", 5, 120),
                ("per_symbol_cooldown_seconds", 10, 3600),
                ("max_concurrent_fetches", 1, 12),
                ("max_concurrent_decisions", 1, 2),
            ):
                if key in new_settings:
                    self.settings[key] = max(minimum, min(maximum, int(new_settings[key])))

            if "shadow_mode" in new_settings:
                self.settings["shadow_mode"] = bool(new_settings["shadow_mode"])

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

            self._fetch_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_fetches"]))
            self._decision_semaphore = asyncio.Semaphore(int(self.settings["max_concurrent_decisions"]))
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
            symbols = list(self.settings.get("symbols", []))
            if current_symbol:
                symbols.append(current_symbol)
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
                self._active_trades.clear()
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

    async def _fetch_timeframe_snapshot(self, symbol: str, timeframe: str) -> Dict[str, Any]:
        """Fetch, validate, and calculate one closed-candle timeframe."""
        from .indicators import compute_all_indicators

        async with self._fetch_semaphore:
            raw_klines = await self.market_service.fetch_klines(
                symbol=symbol,
                interval=timeframe,
                limit=150,
            )
        if not raw_klines:
            raise RuntimeError(f"No {timeframe} candles returned for {symbol}")
        calculated = compute_all_indicators(raw_klines)
        latest = dict(calculated["latest"])
        latest["symbol"] = symbol
        latest["interval"] = timeframe
        latest["time"] = calculated.get("series", [{}])[-1].get("time", raw_klines[-1].get("time", 0))
        latest["leverage"] = int(self.settings["leverage"])
        if not latest.get("is_closed", True):
            raise RuntimeError(f"Latest {timeframe} candle for {symbol} is not closed")
        if not _is_fresh_timestamp(latest.get("time"), timeframe):
            raise RuntimeError(f"Latest {timeframe} candle for {symbol} is stale")
        return latest

    async def _analyze_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Analyze one symbol using parallel timeframe data and bounded inference."""
        timeframes = [
            timeframe for timeframe in self.settings.get("timeframes", ["5m"])
            if timeframe in SUPPORTED_TIMEFRAMES
        ] or ["5m"]
        started = time.perf_counter()
        try:
            snapshots = await asyncio.gather(
                *(self._fetch_timeframe_snapshot(symbol, timeframe) for timeframe in timeframes),
                return_exceptions=True,
            )
            latest_by_timeframe: Dict[str, Dict[str, Any]] = {}
            for timeframe, snapshot in zip(timeframes, snapshots):
                if isinstance(snapshot, Exception):
                    raise snapshot
                latest_by_timeframe[timeframe] = snapshot

            entry_timeframe = "5m" if "5m" in latest_by_timeframe else timeframes[-1]
            state = dict(latest_by_timeframe[entry_timeframe])
            higher = latest_by_timeframe.get("1h") or latest_by_timeframe.get("15m")
            if higher:
                state["htf"] = {
                    "trend": higher.get("trend_state", "NEUTRAL"),
                    "interval": higher.get("interval"),
                    "rsi": higher.get("rsi"),
                    "adx": higher.get("adx"),
                }
            state["multi_timeframe"] = {
                timeframe: {
                    "time": snapshot.get("time"),
                    "trend": snapshot.get("trend_state"),
                    "rsi": snapshot.get("rsi"),
                    "adx": snapshot.get("adx"),
                }
                for timeframe, snapshot in latest_by_timeframe.items()
            }

            async with self._decision_semaphore:
                decision = await self.laya_engine.decide(state)
            signal = self.signal_generator.generate_signal(state, decision)
            signal["analysis_timestamp"] = time.time()
            signal["analysis_symbol"] = symbol
            signal["analysis_timeframes"] = list(timeframes)
            signal["analysis_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            self._analysis_results[symbol] = {
                "symbol": symbol,
                "state": "READY",
                "action": signal.get("action", "NEUTRAL"),
                "confidence_pct": signal.get("confidence_pct", 0.0),
                "last_candle_timestamp": max(
                    snapshot.get("time", 0) for snapshot in latest_by_timeframe.values()
                ),
                "latency_ms": signal["analysis_latency_ms"],
                "timeframes": list(timeframes),
            }
            return {
                "symbol": symbol,
                "signal": signal,
                "latest": state,
                "timeframes": list(timeframes),
            }
        except Exception as exc:
            self._analysis_results[symbol] = {
                "symbol": symbol,
                "state": "ERROR",
                "error": str(exc),
                "last_candle_timestamp": 0,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "timeframes": list(timeframes),
            }
            logger.warning("Parallel analysis failed for %s: %s", symbol, exc)
            return None

    async def _scan_candidates(self, open_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Analyze configured symbols concurrently and return ranked candidates."""
        open_keys = {
            _position_key(position.get("rawSymbol") or position.get("symbol"))
            for position in open_positions
        }
        now = time.time()
        symbols = [
            symbol for symbol in self.settings.get("symbols", [])
            if _position_key(symbol) not in open_keys
            and now >= self._symbol_cooldowns.get(_position_key(symbol), 0.0)
        ]
        results = await asyncio.gather(
            *(self._analyze_symbol(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        candidates: List[Dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict) or not result:
                continue
            signal = result["signal"]
            action = str(signal.get("action", "")).upper()
            confidence = float(signal.get("confidence_pct", 0.0))
            if action in {"NEUTRAL", "WAIT", ""}:
                continue
            if "LONG" not in action and "SHORT" not in action:
                continue
            if confidence < float(self.settings["min_confidence"]):
                continue
            if time.time() - float(signal.get("analysis_timestamp", 0.0)) > float(self.settings["signal_ttl_seconds"]):
                continue
            candidates.append(result)
        candidates.sort(key=lambda item: float(item["signal"].get("confidence_pct", 0.0)), reverse=True)
        return candidates

    async def _manage_position(self, position: Dict[str, Any]) -> None:
        """Supervise one exchange position independently from other symbols."""
        symbol = position.get("rawSymbol") or position.get("symbol")
        display_symbol = position.get("displaySymbol") or symbol
        side = str(position.get("side", "LONG")).upper()
        key = _position_key(symbol)
        trade = self._active_trades.get(key)
        if trade is None:
            trade = {
                "symbol": display_symbol,
                "exchange_symbol": symbol,
                "side": side,
                "entry_price": float(position.get("entryPrice", 0.0)),
                "quantity": float(position.get("quantity", 0.0)),
                "margin": float(position.get("margin", 0.0)),
                "stop_loss": float(position.get("stopLoss", 0.0)),
                "take_profit_1": float(position.get("takeProfit1", position.get("takeProfit", 0.0))),
                "take_profit_2": float(position.get("takeProfit2", 0.0)),
                "be_moved": False,
                "open_time": time.time(),
            }
            self._set_active_trade(trade)

        analysis_result = await self._analyze_symbol(display_symbol)
        analysis = analysis_result["signal"] if analysis_result else None
        entry_price = float(position.get("entryPrice", trade.get("entry_price", 0.0)))
        mark_price = float(position.get("markPrice", entry_price))
        pnl_pct = float(position.get("pnlPercentage", 0.0))

        if self.settings.get("auto_bracket", True):
            stop_loss = float(trade.get("stop_loss", 0.0))
            take_profit = float(trade.get("take_profit_1", 0.0))
            if analysis and analysis.get("targets"):
                targets = analysis["targets"]
                stop_loss = stop_loss or float(analysis.get("stop_loss") or targets.get("stop_loss", 0.0))
                take_profit = take_profit or float(analysis.get("take_profit_1") or targets.get("take_profit_1", 0.0))
            if (stop_loss <= 0 or take_profit <= 0) and entry_price > 0:
                distance = 0.25 / max(int(self.settings.get("leverage", 50)), 1)
                if side == "LONG":
                    stop_loss = stop_loss or entry_price * (1.0 - distance)
                    take_profit = take_profit or entry_price * (1.0 + distance * 1.6)
                else:
                    stop_loss = stop_loss or entry_price * (1.0 + distance)
                    take_profit = take_profit or entry_price * (1.0 - distance * 1.6)
                try:
                    result = await self.toobit_client.set_position_tpsl(
                        symbol=symbol,
                        side=side,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
                    if result.get("success"):
                        trade["stop_loss"] = float(result.get("stopLoss", stop_loss))
                        trade["take_profit_1"] = float(result.get("takeProfit", take_profit))
                        self.add_log("success", "BRACKET", f"Exchange TP/SL confirmed for {side} {display_symbol}.")
                    else:
                        self.add_log("danger", "BRACKET", f"Exchange TP/SL was not confirmed for {display_symbol}.")
                except Exception as exc:
                    self.add_log("danger", "BRACKET", f"Could not confirm exchange TP/SL for {display_symbol}: {exc}")

        if self.settings.get("auto_breakeven", True) and not trade.get("be_moved"):
            take_profit = float(trade.get("take_profit_1", 0.0))
            should_move = (pnl_pct >= 9.0 or (side == "LONG" and mark_price >= take_profit > 0)
                           or (side == "SHORT" and mark_price <= take_profit > 0))
            if should_move and entry_price > 0:
                new_stop = entry_price * 1.0005 if side == "LONG" else entry_price * 0.9995
                try:
                    result = await self.toobit_client.set_position_tpsl(
                        symbol=symbol,
                        side=side,
                        stop_loss=new_stop,
                        take_profit=take_profit,
                    )
                    if result.get("success"):
                        trade["be_moved"] = True
                        trade["stop_loss"] = new_stop
                        self.add_log("success", "PROFIT_LOCK", f"Break-even stop confirmed for {display_symbol}.")
                except Exception as exc:
                    logger.warning("Could not move break-even stop for %s: %s", display_symbol, exc)

        if self.settings.get("opposing_reversal_exit", True) and analysis:
            action = str(analysis.get("action", "")).upper()
            confidence = float(analysis.get("confidence_pct", 0.0))
            opposed = (side == "LONG" and "SHORT" in action) or (side == "SHORT" and "LONG" in action)
            if opposed and confidence >= 70.0:
                result = await self.toobit_client.flash_close(symbol)
                if result.get("success"):
                    self._handle_trade_closed(position, reason=f"opposing {action}")
                return

        if self.settings.get("mode") == "demo":
            stop_loss = float(position.get("stopLoss", trade.get("stop_loss", 0.0)))
            take_profit = float(position.get("takeProfit2", trade.get("take_profit_2", 0.0)))
            hit = (side == "LONG" and ((stop_loss > 0 and mark_price <= stop_loss) or (take_profit > 0 and mark_price >= take_profit)))
            hit = hit or (side == "SHORT" and ((stop_loss > 0 and mark_price >= stop_loss) or (take_profit > 0 and mark_price <= take_profit)))
            if hit:
                result = await self.toobit_client.flash_close(symbol)
                if result.get("success"):
                    self._handle_trade_closed(position, reason="demo SL/TP")

    async def _execute_candidate(self, candidate: Dict[str, Any]) -> None:
        """Validate, size, and dispatch one ranked candidate under the portfolio lock."""
        symbol = candidate["symbol"]
        signal = candidate["signal"]
        latest = candidate.get("latest", {})
        action = str(signal.get("action", "")).upper()
        if "LONG" in action:
            side, position_side, order_side = "LONG", "LONG", "BUY"
        elif "SHORT" in action:
            side, position_side, order_side = "SHORT", "SHORT", "SELL"
        else:
            return
        del side
        entry_price = float(signal.get("entry_price", latest.get("price", latest.get("close", 0.0))))
        if entry_price <= 0:
            return

        balance = await self.toobit_client.get_balance()
        available_margin = float(balance.get("availableBalance", 0.0))
        total_equity = float(balance.get("totalEquity", balance.get("totalBalance", 0.0)))
        if available_margin < 0.50:
            self.add_log("warning", "ORDER", f"Pre-flight skipped for {symbol}: insufficient available margin.")
            return
        if self.settings["sizing_mode"] == "pct_equity":
            target_margin = max(0.50, total_equity * float(self.settings["equity_risk_pct"]) / 100.0)
        else:
            target_margin = max(0.50, float(self.settings["fixed_margin_usd"]))
        target_margin = min(target_margin, available_margin * 0.20)
        leverage = int(self.settings["leverage"])
        notional_value = target_margin * leverage
        rules = await self.toobit_client.get_symbol_rules(symbol)
        multiplier = float(rules.get("multiplier", 1.0))
        step_size = max(float(rules.get("step_size", 0.001)), 1e-8)
        min_qty = float(rules.get("min_qty", 0.001))
        contract_unit_value = entry_price * multiplier
        if contract_unit_value <= 0 or notional_value <= 0:
            return
        raw_quantity = notional_value / contract_unit_value
        step_text = f"{step_size:.8f}".rstrip("0")
        decimals = len(step_text.split(".")[1]) if "." in step_text else 0
        quantity = round((raw_quantity // step_size) * step_size, decimals)
        if quantity < min_qty:
            minimum_margin = (min_qty * contract_unit_value) / leverage
            if minimum_margin > available_margin * 0.40:
                self.add_log("warning", "ORDER", f"Skipping {symbol}: exchange minimum quantity exceeds safety cap.")
                return
            quantity = min_qty
        final_margin = (quantity * contract_unit_value) / leverage
        targets = signal.get("targets", {})
        stop_loss = float(signal.get("stop_loss", targets.get("stop_loss", 0.0)))
        take_profit_1 = float(signal.get("take_profit_1", targets.get("take_profit_1", 0.0)))
        take_profit_2 = float(signal.get("take_profit_2", targets.get("take_profit_2", 0.0)))
        if stop_loss <= 0 or take_profit_1 <= 0:
            self.add_log("warning", "ORDER", f"Skipping {symbol}: signal is missing a valid SL or TP1.")
            return

        async with self._lock:
            result = await self.toobit_client.create_order(
                symbol=symbol,
                side=order_side,
                order_type="MARKET",
                quantity=quantity,
                leverage=leverage,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
            )
            if not result.get("success"):
                self.add_log("danger", "ORDER", f"Order execution failed for {symbol}: {result.get('message', 'rejected')}")
                return
            self._set_active_trade({
                "symbol": symbol,
                "exchange_symbol": symbol,
                "side": position_side,
                "quantity": quantity,
                "entry_price": entry_price,
                "margin": final_margin,
                "stop_loss": stop_loss,
                "take_profit_1": take_profit_1,
                "take_profit_2": take_profit_2,
                "be_moved": False,
                "open_time": time.time(),
            })
            self._symbol_cooldowns[_position_key(symbol)] = time.time() + float(self.settings["per_symbol_cooldown_seconds"])
            self.stats["total_trades"] += 1
            self.stats["last_trade_time"] = time.time()
            self.status = "IN_POSITION"
            self.status_message = f"Opened {leverage}x {position_side} on {symbol}"
            self._save_state()
            self.add_log("success", "ORDER", f"Position opened: {leverage}x {position_side} {quantity} {symbol}.")

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
            self.status = "IN_POSITION"
            self.status_message = f"Managing {len(open_positions)} position(s) across {', '.join(position.get('displaySymbol', '') for position in open_positions)}"
            for position in open_positions:
                await self._manage_position(position)
            self._save_state()

        open_keys = {
            _position_key(position.get("rawSymbol") or position.get("symbol"))
            for position in open_positions
        }
        for key, trade in list(self._active_trades.items()):
            if key not in open_keys:
                self._remove_active_trade(trade.get("symbol", key))

        if len(open_positions) >= int(self.settings["max_positions"]):
            self.status = "IN_POSITION"
            self.status_message = f"Maximum {self.settings['max_positions']} positions reached."
            return

        now = time.time()
        if now < self.stats["cooldown_until"]:
            remaining = int(self.stats["cooldown_until"] - now)
            self.status = "COOLDOWN"
            self.status_message = f"Cooldown active ({remaining}s remaining)."
            return
        if now - self._last_scan_time < float(self.settings["scan_interval_seconds"]):
            return
        self._last_scan_time = now
        self.status = "SCANNING"
        self.status_message = f"Scanning {len(self.settings['symbols'])} symbols in parallel..."
        candidates = await self._scan_candidates(open_positions)
        if not candidates:
            self.status = "IDLE"
            self.status_message = "No fresh multi-timeframe candidates."
            return
        candidate = candidates[0]
        if self.settings.get("shadow_mode", True):
            signal = candidate["signal"]
            self.status = "SHADOW"
            self.status_message = (
                f"Shadow candidate: {signal.get('action')} {candidate['symbol']} "
                f"({signal.get('confidence_pct', 0.0):.1f}%)"
            )
            self.add_log("info", "SCAN", self.status_message)
            return
        await self._execute_candidate(candidate)
        return

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
                latest["leverage"] = int(self.settings["leverage"])
                if not latest.get("is_closed", True):
                    return
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

                    raw_sym = pos.get("rawSymbol") or pos.get("symbol")
                    try:
                        tpsl_res = await self.toobit_client.set_position_tpsl(
                            symbol=raw_sym,
                            side=pos_side,
                            stop_loss=calc_sl,
                            take_profit=calc_tp
                        )
                        if tpsl_res.get("success"):
                            self._active_trade["stop_loss"] = calc_sl
                            self._active_trade["take_profit_1"] = calc_tp
                            self.add_log(
                                "success",
                                "BRACKET",
                                f"🎯 Exchange TP/SL Active: SL ${calc_sl:.2f} | TP ${calc_tp:.2f} armed directly on Toobit for {pos_side} {pos_sym}!"
                            )
                        else:
                            self.add_log(
                                "danger",
                                "BRACKET",
                                f"Exchange TP/SL was not confirmed for {pos_sym}: {tpsl_res.get('message', 'unknown error')}",
                            )
                    except Exception as e:
                        self.add_log(
                            "danger",
                            "BRACKET",
                            f"Could not auto-arm position TP/SL for {pos_sym}: {e}",
                        )

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
            latest["leverage"] = int(self.settings["leverage"])
            if not latest.get("is_closed", True):
                return

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
        self._remove_active_trade(sym)
        self._symbol_cooldowns[_position_key(sym)] = time.time() + int(self.settings["cooldown_seconds"])
        self._save_state()

        self.add_log(
            lvl,
            "SYSTEM",
            f"Trade Finalized [{reason}]: {side} {sym} -> {outcome}. Cooldown active for {cooldown}s."
        )

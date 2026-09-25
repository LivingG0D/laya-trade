import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .config import (
    DEFAULT_EXCHANGE,
    DEFAULT_SYMBOL,
    SUPPORTED_SYMBOLS,
    DEFAULT_TIMEFRAME,
    SUPPORTED_TIMEFRAMES,
    WEB_DIR,
    BASE_DIR,
    APP_HOST,
    APP_PORT,
    TOOBIT_API_KEY,
    TOOBIT_API_SECRET
)
from .market_data import MarketDataService
from .indicators import compute_all_indicators
from .laya_engine import LayaEngine
from .signal_generator import SignalGenerator

from pydantic import BaseModel
from .toobit_client import ToobitClient, generate_position_suggestion
from .auto_trader import AutoTrader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

# Global instances
market_service = MarketDataService()
laya_engine = LayaEngine()
signal_generator = SignalGenerator()
toobit_client = ToobitClient(
    api_key=TOOBIT_API_KEY,
    api_secret=TOOBIT_API_SECRET,
    is_demo=not bool(TOOBIT_API_KEY and TOOBIT_API_SECRET)
)

# Cache latest market analysis for instant position suggestion evaluation
latest_analysis_cache: Dict[str, Any] = {}

class ToobitConnectRequest(BaseModel):
    apiKey: str = ""
    apiSecret: str = ""
    isDemo: bool = False

class ToobitCloseRequest(BaseModel):
    symbol: str

class ToobitSimulateRequest(BaseModel):
    symbol: str = DEFAULT_SYMBOL
    side: str = "LONG"
    quantity: float = 100000.0
    entryPrice: Optional[float] = None
    leverage: int = 50
    stopLoss: Optional[float] = None
    takeProfit1: Optional[float] = None
    takeProfit2: Optional[float] = None

class ToobitTPSLRequest(BaseModel):
    symbol: str
    side: Optional[str] = "LONG"
    stopLoss: float = 0.0
    takeProfit: float = 0.0

class AutoTradeToggleRequest(BaseModel):
    enabled: bool

class AutoTradeSettingsRequest(BaseModel):
    settings: Dict[str, Any]

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        msg_str = json.dumps(message)
        dead = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(msg_str)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.active_connections.discard(d)

ws_manager = ConnectionManager()

# Autonomous Algorithmic Trading Supervisor
auto_trader = AutoTrader(
    toobit_client=toobit_client,
    market_service=market_service,
    laya_engine=laya_engine,
    signal_generator=signal_generator,
    ws_manager=ws_manager
)

# Background tick handler
async def handle_candle_tick(candle_update: Dict[str, Any]):
    """Broadcast tick to all connected web clients."""
    await ws_manager.broadcast({
        "type": "candle_tick",
        "data": candle_update
    })

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing Laya Trade Analyzer services...")
    market_service.subscribe(handle_candle_tick)
    await market_service.start_streaming(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

    # Check model readiness asynchronously without blocking startup
    asyncio.create_task(laya_engine.start_server())

    # Launch AutoTrader background supervisor
    auto_trader.start_loop()

    yield

    # Shutdown
    logger.info("Shutting down services...")
    await auto_trader.stop_loop()
    await market_service.close()
    await laya_engine.close()
    await toobit_client.close()

app = FastAPI(title="Laya Trade Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files & Web UI
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/")
async def get_index():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"status": "running", "message": "Web UI index.html not found"})

@app.get("/api/status")
async def get_status():
    return {
        "status": "online",
        "exchange": market_service.exchange,
        "current_symbol": market_service.current_symbol,
        "current_interval": market_service.current_interval,
        "model_file": laya_engine.model_filename,
        "model_downloaded": laya_engine.is_model_downloaded(),
        "laya_server_active": laya_engine.is_ready,
        "device": laya_engine.device,
        "supported_symbols": SUPPORTED_SYMBOLS,
        "supported_timeframes": SUPPORTED_TIMEFRAMES,
    }

@app.get("/api/symbols")
async def get_symbols():
    return {
        "symbols": SUPPORTED_SYMBOLS,
        "default": DEFAULT_SYMBOL,
        "exchange": market_service.exchange
    }

@app.get("/api/ticker")
async def get_ticker(symbol: str = Query(DEFAULT_SYMBOL)):
    try:
        data = await market_service.fetch_ticker_24h(symbol=symbol)
        return data
    except Exception as e:
        logger.error(f"Error serving ticker: {e}")
        return {}

@app.get("/api/klines")
async def get_klines(
    symbol: str = Query(DEFAULT_SYMBOL),
    interval: str = Query(DEFAULT_TIMEFRAME),
    limit: int = Query(300, ge=50, le=1000)
):
    try:
        raw_klines = await market_service.fetch_klines(symbol=symbol, interval=interval, limit=limit)
        calculated = compute_all_indicators(raw_klines)
        
        # Attach any markers for this symbol/interval from history (deduplicated & sorted)
        marker_map = {}
        for s in signal_generator.signal_history:
            if s.get("symbol") == symbol.upper() and s.get("interval") == interval and s.get("marker"):
                m = s["marker"]
                marker_map[m["time"]] = m
        active_markers = sorted(marker_map.values(), key=lambda x: x["time"])

        # Asynchronously fetch institutional metrics (HTF, depth, funding) to enrich latest telemetry
        try:
            htf_ctx, depth_ctx, funding_ctx = await asyncio.gather(
                market_service.fetch_htf_context(symbol),
                market_service.fetch_order_book_depth(symbol),
                market_service.fetch_funding_rate(symbol),
                return_exceptions=True
            )
            if isinstance(htf_ctx, dict) and not isinstance(htf_ctx, Exception):
                calculated["latest"]["htf"] = htf_ctx
            if isinstance(depth_ctx, dict) and not isinstance(depth_ctx, Exception):
                calculated["latest"]["depth"] = depth_ctx
            if isinstance(funding_ctx, dict) and not isinstance(funding_ctx, Exception):
                calculated["latest"]["funding"] = funding_ctx
        except Exception as e:
            logger.debug(f"Non-critical institutional enrichment in klines: {e}")

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "series": calculated["series"],
            "latest": calculated["latest"],
            "markers": active_markers
        }
    except Exception as e:
        logger.error(f"Error serving klines: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analyze")
async def trigger_analysis(
    symbol: str = Query(DEFAULT_SYMBOL),
    interval: str = Query(DEFAULT_TIMEFRAME)
):
    """Run full technical calculation and Laya System 1 probabilistic decision with multi-factor confluence."""
    try:
        # Fully parallel gather across all external exchange network endpoints
        results = await asyncio.gather(
            market_service.fetch_klines(symbol=symbol, interval=interval, limit=200),
            market_service.fetch_htf_context(symbol),
            market_service.fetch_order_book_depth(symbol),
            market_service.fetch_funding_rate(symbol),
            return_exceptions=True
        )
        raw_klines, htf_ctx, depth_ctx, funding_ctx = results

        if isinstance(raw_klines, Exception) or not raw_klines:
            logger.error(f"Failed to fetch market klines: {raw_klines}")
            raise HTTPException(status_code=500, detail="Failed to fetch market klines from exchange")

        calc = compute_all_indicators(raw_klines)
        latest = calc["latest"]
        latest["symbol"] = symbol.upper()
        latest["interval"] = interval
        latest["time"] = calc["series"][-1]["time"] if calc["series"] else 0

        if isinstance(htf_ctx, dict) and not isinstance(htf_ctx, Exception):
            latest["htf"] = htf_ctx
        if isinstance(depth_ctx, dict) and not isinstance(depth_ctx, Exception):
            latest["depth"] = depth_ctx
        if isinstance(funding_ctx, dict) and not isinstance(funding_ctx, Exception):
            latest["funding"] = funding_ctx

        # Laya inference with enriched multi-factor context
        laya_decision = await laya_engine.decide(latest)

        # Generate institutional grade trade setup with strict confluence gating
        signal = signal_generator.generate_signal(latest, laya_decision)

        # Cache for fast AI position suggestion evaluation
        latest_analysis_cache["latest"] = latest
        latest_analysis_cache["signal"] = signal
        latest_analysis_cache["laya"] = laya_decision
        latest_analysis_cache["symbol"] = symbol.upper()

        # Broadcast signal to WebSocket clients
        await ws_manager.broadcast({
            "type": "new_signal",
            "signal": signal,
            "latest": latest,
            "laya": laya_decision
        })

        return {
            "signal": signal,
            "latest": latest,
            "laya": laya_decision
        }
    except Exception as e:
        logger.error(f"Error in analyze: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history")
async def get_history():
    return {
        "history": signal_generator.signal_history
    }

# ==============================================
# TOOBIT API & POSITION MANAGEMENT ENDPOINTS
# ==============================================

@app.post("/api/toobit/connect")
async def connect_toobit(req: ToobitConnectRequest):
    """Connect to Toobit via API key/secret or activate simulated Demo mode."""
    if req.isDemo:
        toobit_client.set_credentials("", "", is_demo=True)
        return {
            "success": True,
            "mode": "demo",
            "message": "Switched to Simulated Demo Account (Virtual $5,000 USDT Margin)."
        }
    
    if not req.apiKey or not req.apiSecret:
        raise HTTPException(status_code=400, detail="Both API Key and Secret are required for live connection.")
    
    # Test connection with Toobit
    toobit_client.set_credentials(req.apiKey, req.apiSecret, is_demo=False)
    test_result = await toobit_client.test_connection()
    if not test_result.get("success"):
        toobit_client.disconnect()
        return JSONResponse(status_code=401, content=test_result)

    # Persist validated keys to .env
    try:
        env_path = BASE_DIR / ".env"
        existing_lines = []
        if env_path.exists():
            existing_lines = [
                line for line in env_path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("TOOBIT_API_KEY=") and not line.startswith("TOOBIT_API_SECRET=")
            ]
        existing_lines.append(f"TOOBIT_API_KEY={req.apiKey.strip()}")
        existing_lines.append(f"TOOBIT_API_SECRET={req.apiSecret.strip()}")
        env_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist keys to .env: {e}")

    return {
        "success": True,
        "mode": "live",
        "message": "Successfully authenticated with Toobit Futures API!"
    }

@app.post("/api/toobit/disconnect")
async def disconnect_toobit():
    """Disconnect and clear credentials."""
    toobit_client.disconnect()
    try:
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            existing_lines = [
                line for line in env_path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("TOOBIT_API_KEY=") and not line.startswith("TOOBIT_API_SECRET=")
            ]
            env_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not clear keys from .env: {e}")
    return {"success": True, "message": "Disconnected from Toobit."}

@app.get("/api/toobit/status")
async def get_toobit_status():
    """Get active Toobit connection mode, balance, and account overview."""
    mode = "demo" if toobit_client.is_demo else ("live" if toobit_client.api_key else "disconnected")
    balance = await toobit_client.get_balance() if toobit_client.is_connected else {}
    return {
        "connected": toobit_client.is_connected,
        "mode": mode,
        "balance": balance
    }

@app.get("/api/toobit/positions")
async def get_toobit_positions(symbol: Optional[str] = Query(None)):
    """Fetch active open positions with live calculated PnL."""
    try:
        # Fetch all open positions across the Toobit account
        all_positions = await toobit_client.get_positions(symbol=None)
        
        # If symbol specified, filter/prioritize matching position
        matching_positions = []
        if symbol and all_positions:
            norm_target = symbol.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
            for p in all_positions:
                p_sym = p.get("symbol", "")
                norm_pos = p_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
                if norm_target == norm_pos or p_sym == symbol:
                    matching_positions.append(p)

        # If auto_trader has active trade info with TP/SL, enrich positions
        if auto_trader._active_trade:
            act = auto_trader._active_trade
            act_sym = act.get("symbol", "")
            norm_act = act_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
            for p in all_positions:
                p_sym = p.get("symbol", "")
                norm_p = p_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
                if norm_p == norm_act:
                    if (not p.get("stopLoss") or p.get("stopLoss") == 0) and act.get("stop_loss"):
                        p["stopLoss"] = float(act["stop_loss"])
                    if (not p.get("takeProfit") or p.get("takeProfit") == 0) and act.get("take_profit_1"):
                        p["takeProfit"] = float(act["take_profit_1"])
                        p["takeProfit1"] = float(act["take_profit_1"])

        # Active positions to return:
        # If there is a position for the active chart coin, return it first.
        # Otherwise, return all open account positions so the trader is never blind to active trades!
        positions_to_return = matching_positions if matching_positions else all_positions

        return {
            "connected": toobit_client.is_connected,
            "mode": "demo" if toobit_client.is_demo else "live",
            "positions": positions_to_return,
            "all_positions": all_positions,
            "has_matching_position": bool(matching_positions)
        }
    except Exception as e:
        logger.error(f"Error fetching Toobit positions: {e}")
        return {"connected": toobit_client.is_connected, "positions": [], "all_positions": [], "error": str(e)}

@app.post("/api/toobit/tpsl")
async def set_toobit_tpsl(req: ToobitTPSLRequest):
    """Directly configure or adjust exchange-level TP/SL on Toobit."""
    try:
        side = req.side
        if not side:
            side = await toobit_client._resolve_position_side(req.symbol) or "LONG"
        res = await toobit_client.set_position_tpsl(
            symbol=req.symbol,
            side=side,
            stop_loss=req.stopLoss,
            take_profit=req.takeProfit
        )
        # Update auto_trader active trade if matching
        if auto_trader._active_trade:
            pos_sym = auto_trader._active_trade.get("symbol", "")
            norm_target = req.symbol.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
            norm_active = pos_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
            if norm_target == norm_active:
                if req.stopLoss > 0:
                    auto_trader._active_trade["stop_loss"] = req.stopLoss
                if req.takeProfit > 0:
                    auto_trader._active_trade["take_profit_1"] = req.takeProfit
        return res
    except Exception as e:
        logger.error(f"Error setting Toobit TP/SL: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/toobit/close")
async def close_toobit_position(req: ToobitCloseRequest):
    """Execute flashClose order on Toobit."""
    try:
        res = await toobit_client.flash_close(req.symbol)
        return res
    except Exception as e:
        logger.error(f"Error closing position {req.symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/toobit/demo/simulate")
async def simulate_demo_position(req: ToobitSimulateRequest):
    """Open or replace a demo position for testing."""
    if not toobit_client.is_demo:
        toobit_client.set_credentials("", "", is_demo=True)
    
    entry_price = req.entryPrice
    try:
        ticker = await market_service.fetch_ticker_24h(symbol=req.symbol)
        real_price = float(ticker.get("last_price") or ticker.get("price", 0) or 0)
    except Exception:
        real_price = 0.0

    if real_price > 0 and (
        not entry_price or entry_price <= 0
        or abs(entry_price - real_price) / real_price > 0.05
    ):
        entry_price = real_price
    if not entry_price or entry_price <= 0:
        entry_price = 0.0042

    pos = toobit_client.simulate_position(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        entry_price=entry_price,
        leverage=req.leverage,
        stop_loss=float(req.stopLoss or 0.0),
        take_profit_1=float(req.takeProfit1 or 0.0),
        take_profit_2=float(req.takeProfit2 or 0.0),
    )
    return {
        "success": True,
        "message": f"Simulated {req.side} position opened for {req.symbol}!",
        "position": pos
    }

@app.get("/api/toobit/suggestions")
async def get_toobit_suggestions(symbol: Optional[str] = Query(None)):
    """
    Evaluate open positions against the latest Laya System-1 probabilistic intelligence
    and return tailored actionable trade management suggestions.
    """
    # 1. Fetch active positions across account
    all_positions = await toobit_client.get_positions(symbol=None)
    target_sym = symbol or market_service.current_symbol

    # 2. Check for matching position or fallback to open account position
    active_pos = None
    if all_positions:
        norm_target = target_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
        for p in all_positions:
            p_sym = p.get("symbol", "")
            norm_pos = p_sym.upper().replace("-SWAP-", "").replace(".P", "").replace("-", "").replace("_", "")
            if norm_target == norm_pos or p_sym == target_sym:
                active_pos = p
                break
        
        # If no position matches current chart coin, evaluate the actual open position!
        if not active_pos and all_positions:
            active_pos = all_positions[0]
            target_sym = active_pos.get("displaySymbol") or active_pos.get("symbol")

    ticker = await market_service.fetch_ticker_24h(symbol=target_sym)
    current_price = float(ticker.get("last_price") or ticker.get("price", 0.0))
    if current_price <= 0 and active_pos:
        current_price = float(active_pos.get("markPrice") or active_pos.get("entryPrice", 0.0))
    if current_price <= 0 and latest_analysis_cache.get("latest"):
        current_price = float(latest_analysis_cache["latest"].get("price", 0.0))

    # Get or generate latest market analysis
    analysis = latest_analysis_cache.get("signal")
    laya_dec = latest_analysis_cache.get("laya")

    if not analysis or latest_analysis_cache.get("symbol") != target_sym.upper():
        try:
            raw_klines = await market_service.fetch_klines(symbol=target_sym, interval=market_service.current_interval, limit=100)
            calc = compute_all_indicators(raw_klines)
            latest = calc["latest"]
            latest["symbol"] = target_sym.upper()
            latest["interval"] = market_service.current_interval
            latest["time"] = calc["series"][-1]["time"] if calc["series"] else 0
            laya_dec = await laya_engine.decide(latest)
            analysis = signal_generator.generate_signal(latest, laya_dec)
            
            latest_analysis_cache["latest"] = latest
            latest_analysis_cache["signal"] = analysis
            latest_analysis_cache["laya"] = laya_dec
            latest_analysis_cache["symbol"] = target_sym.upper()
        except Exception as e:
            logger.warning(f"Could not compute fresh analysis for suggestion: {e}")

    suggestion = generate_position_suggestion(
        position=active_pos,
        current_price=current_price,
        signal=analysis,
        laya_decision=laya_dec
    )

    return {
        "symbol": target_sym,
        "currentPrice": current_price,
        "position": active_pos,
        "suggestion": suggestion
    }

# ==============================================
# AUTONOMOUS TRADING ENGINE ENDPOINTS
# ==============================================

@app.get("/api/autotrade/status")
async def get_autotrade_status():
    """Return live AutoTrader state, settings, stats, and audit logs."""
    return auto_trader.get_status()

@app.post("/api/autotrade/toggle")
async def toggle_autotrade(req: AutoTradeToggleRequest):
    """Enable or disable autonomous trading."""
    return await auto_trader.toggle(req.enabled)

@app.post("/api/autotrade/settings")
async def update_autotrade_settings(req: AutoTradeSettingsRequest):
    """Update AutoTrader strategy, risk, and circuit breaker settings."""
    return await auto_trader.update_settings(req.settings)

@app.post("/api/autotrade/emergency_stop")
async def emergency_stop_autotrade():
    """Emergency Kill Switch: instantly halts auto-trading, cancels orders, and flash-closes positions."""
    return await auto_trader.emergency_stop()

@app.post("/api/autotrade/clear_logs")
async def clear_autotrade_logs():
    """Clear AutoTrader audit log history."""
    return auto_trader.clear_logs()

@app.post("/api/switch")
async def switch_stream(symbol: str = Query(...), interval: str = Query(DEFAULT_TIMEFRAME)):
    sym = symbol.upper()
    if sym not in SUPPORTED_SYMBOLS:
        SUPPORTED_SYMBOLS.append(sym)
    await market_service.start_streaming(sym, interval)
    return {
        "status": "success",
        "symbol": sym,
        "interval": interval
    }

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                data = json.loads(data_text)
                action = data.get("action")
                if action == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                elif action == "analyze":
                    sym = data.get("symbol", market_service.current_symbol)
                    intv = data.get("interval", market_service.current_interval)
                    raw_klines = await market_service.fetch_klines(symbol=sym, interval=intv, limit=200)
                    calc = compute_all_indicators(raw_klines)
                    latest = calc["latest"]
                    latest["symbol"] = sym
                    latest["interval"] = intv
                    latest["time"] = calc["series"][-1]["time"]
                    decision = await laya_engine.decide(latest)
                    sig = signal_generator.generate_signal(latest, decision)
                    await websocket.send_text(json.dumps({
                        "type": "new_signal",
                        "signal": sig,
                        "latest": latest,
                        "laya": decision
                    }))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket client error: {e}")
        ws_manager.disconnect(websocket)

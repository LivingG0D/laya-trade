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
    APP_HOST,
    APP_PORT
)
from .market_data import MarketDataService
from .indicators import compute_all_indicators
from .laya_engine import LayaEngine
from .signal_generator import SignalGenerator

from pydantic import BaseModel
from .toobit_client import ToobitClient, generate_position_suggestion

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

# Global instances
market_service = MarketDataService()
laya_engine = LayaEngine()
signal_generator = SignalGenerator()
toobit_client = ToobitClient(is_demo=True)  # Start in Demo simulation mode by default for zero-risk testing

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
    leverage: int = 10

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

    yield

    # Shutdown
    logger.info("Shutting down services...")
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
        "laya_server_active": laya_engine._is_server_alive,
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
        raw_klines = await market_service.fetch_klines(symbol=symbol, interval=interval, limit=200)
        calc = compute_all_indicators(raw_klines)
        latest = calc["latest"]
        latest["symbol"] = symbol.upper()
        latest["interval"] = interval
        latest["time"] = calc["series"][-1]["time"] if calc["series"] else 0

        # Concurrent fetch of institutional intelligence: 1h HTF Macro Trend, Order Book Depth (OBI), and Funding Rate
        try:
            htf_ctx, depth_ctx, funding_ctx = await asyncio.gather(
                market_service.fetch_htf_context(symbol),
                market_service.fetch_order_book_depth(symbol),
                market_service.fetch_funding_rate(symbol),
                return_exceptions=True
            )
            if isinstance(htf_ctx, dict) and not isinstance(htf_ctx, Exception):
                latest["htf"] = htf_ctx
            if isinstance(depth_ctx, dict) and not isinstance(depth_ctx, Exception):
                latest["depth"] = depth_ctx
            if isinstance(funding_ctx, dict) and not isinstance(funding_ctx, Exception):
                latest["funding"] = funding_ctx
        except Exception as e:
            logger.warning(f"Error gathering institutional intelligence in analyze: {e}")

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
    
    return {
        "success": True,
        "mode": "live",
        "message": "Successfully authenticated with Toobit Futures API!"
    }

@app.post("/api/toobit/disconnect")
async def disconnect_toobit():
    """Disconnect and clear credentials."""
    toobit_client.disconnect()
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
        sym = symbol or market_service.current_symbol
        ticker = await market_service.fetch_ticker_24h(symbol=sym)
        current_price = float(ticker.get("last_price") or ticker.get("price", 0.0))

        positions = await toobit_client.get_positions(symbol=symbol, current_market_price=current_price)
        return {
            "connected": toobit_client.is_connected,
            "mode": "demo" if toobit_client.is_demo else "live",
            "positions": positions
        }
    except Exception as e:
        logger.error(f"Error fetching Toobit positions: {e}")
        return {"connected": toobit_client.is_connected, "positions": [], "error": str(e)}

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
    if not entry_price or entry_price <= 0:
        ticker = await market_service.fetch_ticker_24h(symbol=req.symbol)
        entry_price = float(ticker.get("last_price") or ticker.get("price", 0.0042))

    pos = toobit_client.simulate_position(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        entry_price=entry_price,
        leverage=req.leverage
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
    target_sym = symbol or market_service.current_symbol
    ticker = await market_service.fetch_ticker_24h(symbol=target_sym)
    current_price = float(ticker.get("last_price") or ticker.get("price", 0.0))
    if current_price <= 0 and latest_analysis_cache.get("latest"):
        current_price = float(latest_analysis_cache["latest"].get("price", 0.0))

    # Get active position
    positions = await toobit_client.get_positions(symbol=target_sym, current_market_price=current_price)
    active_pos = positions[0] if positions else None

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

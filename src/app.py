import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Dict, Any, Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .config import (
    DEFAULT_SYMBOL,
    SUPPORTED_SYMBOLS,
    DEFAULT_TIMEFRAME,
    SUPPORTED_TIMEFRAMES,
    WEB_DIR,
    BASE_DIR,
    TOOBIT_API_KEY,
    TOOBIT_API_SECRET,
    DEFAULT_POSITION_MARGIN_USD,
)
from .market_data import MarketDataService, symbol_key
from .indicators import compute_all_indicators
from .laya_engine import LayaEngine
from .signal_generator import SignalGenerator

from pydantic import BaseModel
from .toobit_client import ToobitClient
from .simulated_exchange import SimulatedExchange
from .position_advice import generate_position_suggestion
from .auto_trader import AutoTrader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

# Global instances
market_service = MarketDataService()
laya_engine = LayaEngine()
signal_generator = SignalGenerator()
toobit_live = ToobitClient(api_key=TOOBIT_API_KEY, api_secret=TOOBIT_API_SECRET)


async def _last_price(symbol: str) -> float:
    ticker = await market_service.fetch_ticker_24h(symbol=symbol)
    return float(ticker.get("last_price") or 0.0)


# The demo account trades on live prices and live contract rules.
simulated_exchange = SimulatedExchange(price_source=_last_price, rules_source=toobit_live.get_symbol_rules)
# The venue every route and the auto-trader trade through; switched by _use_exchange.
exchange = toobit_live if toobit_live.is_connected else simulated_exchange


def _exchange_mode() -> str:
    return "demo" if exchange.is_demo else ("live" if exchange.api_key else "disconnected")

# Cache latest market analysis for instant position suggestion evaluation
latest_analysis_cache: Dict[str, Any] = {}


def _attach_context(latest: Dict[str, Any], htf: Any, depth: Any, funding: Any) -> None:
    """Attach HTF / order-book / funding context; failed fetches arrive as exceptions and are skipped."""
    for key, ctx in (("htf", htf), ("depth", depth), ("funding", funding)):
        if isinstance(ctx, dict):
            latest[key] = ctx


async def _run_analysis(raw_klines, symbol: str, interval: str, context=()) -> tuple:
    """Indicators -> Laya decision -> signal, cached for the position-suggestion endpoint."""
    calc = compute_all_indicators(raw_klines)
    latest = calc["latest"]
    latest["symbol"] = symbol.upper()
    latest["interval"] = interval
    latest["time"] = calc["series"][-1]["time"] if calc["series"] else 0
    if context:
        _attach_context(latest, *context)
    laya_decision = await laya_engine.decide(latest)
    signal = signal_generator.generate_signal(latest, laya_decision)
    latest_analysis_cache.update(latest=latest, signal=signal, laya=laya_decision, symbol=symbol.upper())
    return latest, laya_decision, signal


def _write_toobit_keys(api_key: str = "", api_secret: str = "") -> None:
    """Drop TOOBIT_API_KEY/SECRET from .env, then append the new pair if given (creates .env only then)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists() and not api_key:
        return
    lines = [
        line for line in (env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else [])
        if not line.startswith(("TOOBIT_API_KEY=", "TOOBIT_API_SECRET="))
    ]
    if api_key:
        lines += [f"TOOBIT_API_KEY={api_key}", f"TOOBIT_API_SECRET={api_secret}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

class ToobitConnectRequest(BaseModel):
    apiKey: str = ""
    apiSecret: str = ""
    isDemo: bool = False

class ToobitCloseRequest(BaseModel):
    symbol: str

class ToobitSimulateRequest(BaseModel):
    symbol: str = DEFAULT_SYMBOL
    side: str = "LONG"
    quantity: Optional[float] = None  # default: the fixed margin at this leverage
    entryPrice: Optional[float] = None
    leverage: int = 50
    stopLoss: Optional[float] = None
    takeProfit1: Optional[float] = None
    takeProfit2: Optional[float] = None

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
    exchange=exchange,
    market_service=market_service,
    laya_engine=laya_engine,
    signal_generator=signal_generator,
    ws_manager=ws_manager
)


def _use_exchange(adapter) -> None:
    """Point the routes and the auto-trader at ``adapter`` (live Toobit or the simulated exchange)."""
    global exchange
    exchange = adapter
    auto_trader.exchange = adapter

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
    await toobit_live.close()

app = FastAPI(title="Laya Trade Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Web UI files must be revalidated on every load: a cached style.css/app.js next to a
# fresh index.html breaks the page. Unchanged files still answer 304 via their ETag.
@app.middleware("http")
async def revalidate_ui_files(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

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
            _attach_context(calculated["latest"], htf_ctx, depth_ctx, funding_ctx)
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

        latest, laya_decision, signal = await _run_analysis(
            raw_klines, symbol, interval, context=(htf_ctx, depth_ctx, funding_ctx)
        )

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
        _use_exchange(simulated_exchange)
        return {
            "success": True,
            "mode": "demo",
            "message": "Switched to Simulated Demo Account (Virtual $5,000 USDT Margin)."
        }
    
    if not req.apiKey or not req.apiSecret:
        raise HTTPException(status_code=400, detail="Both API Key and Secret are required for live connection.")
    
    # Test connection with Toobit
    toobit_live.set_credentials(req.apiKey, req.apiSecret)
    _use_exchange(toobit_live)
    test_result = await toobit_live.test_connection()
    if not test_result.get("success"):
        toobit_live.disconnect()
        return JSONResponse(status_code=401, content=test_result)

    # Persist validated keys to .env
    try:
        _write_toobit_keys(req.apiKey.strip(), req.apiSecret.strip())
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
    toobit_live.disconnect()
    simulated_exchange.reset()
    _use_exchange(toobit_live)
    try:
        _write_toobit_keys()
    except Exception as e:
        logger.warning(f"Could not clear keys from .env: {e}")
    return {"success": True, "message": "Disconnected from Toobit."}

@app.get("/api/toobit/status")
async def get_toobit_status():
    """Get active Toobit connection mode, balance, and account overview."""
    balance = await exchange.get_balance() if exchange.is_connected else {}
    return {
        "connected": exchange.is_connected,
        "mode": _exchange_mode(),
        "balance": balance
    }

@app.get("/api/toobit/positions")
async def get_toobit_positions(symbol: Optional[str] = Query(None)):
    """Fetch active open positions with live calculated PnL."""
    try:
        # Fetch all open positions across the Toobit account
        all_positions = await exchange.get_positions(symbol=None)

        # If symbol specified, filter/prioritize matching position
        matching_positions = []
        if symbol and all_positions:
            norm_target = symbol_key(symbol)
            for p in all_positions:
                p_sym = p.get("symbol", "")
                norm_pos = symbol_key(p_sym)
                if norm_target == norm_pos or p_sym == symbol:
                    matching_positions.append(p)

        # If AutoTrader has active trade metadata, enrich each matching position.
        for act in auto_trader.get_active_trades():
            act_sym = act.get("symbol", "")
            norm_act = symbol_key(act_sym)
            for p in all_positions:
                p_sym = p.get("symbol", "")
                norm_p = symbol_key(p_sym)
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
            "connected": exchange.is_connected,
            "mode": _exchange_mode(),
            "positions": positions_to_return,
            "all_positions": all_positions,
            "has_matching_position": bool(matching_positions)
        }
    except Exception as e:
        logger.error(f"Error fetching Toobit positions: {e}")
        return {"connected": exchange.is_connected, "positions": [], "all_positions": [], "error": str(e)}

@app.post("/api/toobit/close")
async def close_toobit_position(req: ToobitCloseRequest):
    """Market-close a position; a trade the auto-trader manages is booked toward its loss limits."""
    try:
        return await auto_trader.close_trade(req.symbol, reason="manual close")
    except Exception as e:
        logger.error(f"Error closing position {req.symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/toobit/demo/simulate")
async def simulate_demo_position(req: ToobitSimulateRequest):
    """Open or replace a demo position for testing."""
    if not exchange.is_demo:
        if exchange.api_key:
            # Never flip a connected live account to demo behind the trader's back.
            return JSONResponse(status_code=409, content={
                "success": False,
                "message": "Connected to the live account: switch to demo before simulating positions.",
            })
        _use_exchange(simulated_exchange)

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

    pos = simulated_exchange.simulate_position(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity or DEFAULT_POSITION_MARGIN_USD * req.leverage / entry_price,
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
    all_positions = await exchange.get_positions(symbol=None)
    target_sym = symbol or market_service.current_symbol

    # 2. Check for matching position or fallback to open account position
    active_pos = None
    if all_positions:
        norm_target = symbol_key(target_sym)
        for p in all_positions:
            p_sym = p.get("symbol", "")
            norm_pos = symbol_key(p_sym)
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
            interval = market_service.current_interval
            raw_klines = await market_service.fetch_klines(symbol=target_sym, interval=interval, limit=100)
            _, laya_dec, analysis = await _run_analysis(raw_klines, target_sym, interval)
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

# Perpetual symbols in display ("BTCUSDT.P", "BTCUSDT") or Toobit ("BTC-SWAP-USDT") form.
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,20}(USDT(\.P)?|-SWAP-USDT)$")


@app.post("/api/switch")
async def switch_stream(symbol: str = Query(...), interval: str = Query(DEFAULT_TIMEFRAME)):
    # Streaming any symbol is fine; SUPPORTED_SYMBOLS stays fixed because it is the
    # auto-trader's allow-list, which a chart switch must never widen.
    sym = symbol.upper().strip()
    if not _SYMBOL_PATTERN.match(sym) or interval not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported symbol or interval")
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
            await websocket.receive_text()  # push-only feed: web clients never send
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket client error: {e}")
        ws_manager.disconnect(websocket)

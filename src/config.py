import os
from pathlib import Path
from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MODELS_DIR = BASE_DIR / "models"
WEB_DIR = BASE_DIR / "web"

MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Default Asset & Exchange
DEFAULT_SYMBOL = "BTCUSDT.P"

SUPPORTED_SYMBOLS = [
    # Major Mega Caps (Default)
    "BTCUSDT.P",
    "ETHUSDT.P",
    "SOLUSDT.P",
    "BNBUSDT.P",
    "XRPUSDT.P",
    # Top Trending Memes
    "1000PEPEUSDT.P",
    "DOGEUSDT.P",
    "1000SHIBUSDT.P",
    "1000BONKUSDT.P",
    "1000FLOKIUSDT.P",
    "WIFUSDT.P",
    "POPCATUSDT.P",
    "NEIROUSDT.P",
    "TURBOUSDT.P",
    # High-Performance Layer 1 & 2
    "SUIUSDT.P",
    "AVAXUSDT.P",
    "ADAUSDT.P",
    "NEARUSDT.P",
    "TONUSDT.P",
    "ARBUSDT.P",
    "OPUSDT.P",
    "APTUSDT.P",
    "SEIUSDT.P",
    "DOTUSDT.P",
    "LTCUSDT.P",
    # AI & Compute & Infrastructure
    "TAOUSDT.P",
    "FETUSDT.P",
    "RENDERUSDT.P",
    "LINKUSDT.P",
    "TIAUSDT.P",
    "INJUSDT.P",
    "KASUSDT.P",
    # Toobit Swap Format Aliases
    "1000PEPE-SWAP-USDT",
    "BTC-SWAP-USDT",
    "ETH-SWAP-USDT",
    "SOL-SWAP-USDT",
    "DOGE-SWAP-USDT",
    "XRP-SWAP-USDT",
    "SUI-SWAP-USDT",
    "WIF-SWAP-USDT",
    "1000SHIB-SWAP-USDT"
]

DEFAULT_TIMEFRAME = "5m"
SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Toobit Endpoints & Optional Credentials
TOOBIT_REST_URL = "https://api.toobit.com"
TOOBIT_WS_URL = "wss://stream.toobit.com/quote/ws/v1"
TOOBIT_API_KEY = os.getenv("TOOBIT_API_KEY", "").strip()
TOOBIT_API_SECRET = os.getenv("TOOBIT_API_SECRET", "").strip()

# Binance Futures Endpoints (Fallback)
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com"

# Laya Model Settings
DEFAULT_MODEL_FILE = "laya_crypto_fp8"
HF_REPO_ID = "convaiinnovations/laya"

# Service Ports
APP_PORT = int(os.getenv("APP_PORT", "8000"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# Risk & Strategy Defaults
DEFAULT_LEVERAGE = 50
DEFAULT_ATR_SL_MULTIPLIER = 1.6
DEFAULT_TP1_RR = 0.80         # Primary Scalp Take-Profit at +0.80R (+16% to +20% ROE)
DEFAULT_TP2_RR = 1.40         # Secondary Runner Target at +1.40R (+28% to +35% ROE)
LEVERAGE_MAX_SL_PCT = 0.0080  # 0.80% maximum price move
LEVERAGE_MIN_SL_PCT = 0.0035  # 0.35% minimum buffer (optimal for 5m scalping)

# Anti-Volatility-Drag Position Sizing (Protects against compounding variance drag on 50x)
DEFAULT_POSITION_MARGIN_USD = 10.00   # Fixed base margin for small accounts ($10 per trade)
DEFAULT_KELLY_FRACTION_PCT = 0.20     # Quarter-Kelly maximum equity allocation (max 20% of account balance)


# Barrier exit profile: the event the fine-tuned model was trained to predict
BARRIER_TP_PCT = 0.012
BARRIER_SL_PCT = 0.006
BARRIER_MAX_HOLD_SECONDS = 7200
# Round-trip trading cost (4 bps fee + 2 bps slippage per side), used for the
# break-even offset and the cost filter
ROUND_TRIP_COST_PCT = 0.0012

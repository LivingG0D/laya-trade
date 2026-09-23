import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
BIN_DIR = BASE_DIR / "bin"
WEB_DIR = BASE_DIR / "web"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
BIN_DIR.mkdir(parents=True, exist_ok=True)

# Default Asset & Exchange
DEFAULT_EXCHANGE = "Toobit"
DEFAULT_SYMBOL = "1000PEPEUSDT.P"

SUPPORTED_SYMBOLS = [
    "1000PEPEUSDT.P",
    "BTCUSDT.P",
    "ETHUSDT.P",
    "SOLUSDT.P",
    "DOGEUSDT.P",
    "1000PEPE-SWAP-USDT",
    "BTC-SWAP-USDT",
    "ETH-SWAP-USDT",
    "SOL-SWAP-USDT",
    "DOGE-SWAP-USDT"
]

DEFAULT_TIMEFRAME = "5m"
SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Toobit Endpoints
TOOBIT_REST_URL = "https://api.toobit.com"
TOOBIT_WS_URL = "wss://stream.toobit.com/quote/ws/v1"

# Binance Futures Endpoints (Fallback)
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com"
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"

# Laya Model Settings
HF_REPO_ID = "mys/laya-GGUF"
DEFAULT_MODEL_FILE = "laya_english_q8_0.gguf"
ALTERNATIVE_MODELS = [
    "laya_english_q8_0.gguf",
    "laya_english_f16.gguf",
    "laya_english_ud_q4_k_m.gguf"
]

# Service Ports
LAYA_SERVER_PORT = int(os.getenv("LAYA_SERVER_PORT", "18080"))
LAYA_SERVER_HOST = os.getenv("LAYA_SERVER_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# Risk & Strategy Defaults
DEFAULT_ATR_SL_MULTIPLIER = 1.5
DEFAULT_TP1_RR = 1.5
DEFAULT_TP2_RR = 2.5
ANALYSIS_INTERVAL_SECONDS = 15

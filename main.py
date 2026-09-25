import argparse
import asyncio
import sys
import uvicorn
from pathlib import Path

# Fix Windows console UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from src.config import APP_HOST, APP_PORT, DEFAULT_SYMBOL, DEFAULT_MODEL_FILE, MODELS_DIR
from src.laya_engine import LayaEngine

def parse_args():
    parser = argparse.ArgumentParser(description="Laya Crypto TradingView Chart Analyzer")
    parser.add_argument("--host", type=str, default=APP_HOST, help="Host address to bind")
    parser.add_argument("--port", type=int, default=APP_PORT, help="Port to listen on")
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL, help="Initial crypto symbol")
    parser.add_argument("--download-model", action="store_true", help="Download Laya GGUF model immediately")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn hot reloading")
    return parser.parse_args()

def main():
    args = parse_args()

    print("=" * 65)
    print(" [*] LAYA LIVE CRYPTO TRADINGVIEW CHART ANALYZER")
    print(f" [*] Initial Asset: {args.symbol}")
    print(f" [*] Model: Fine-Tuned Laya Crypto ({DEFAULT_MODEL_FILE})")
    print(f" [*] Dashboard: http://localhost:{args.port}")
    print("=" * 65)

    engine = LayaEngine()
    if not engine.is_model_downloaded():
        print(f"\n[*] Checking Laya model at {engine.model_path}...")
        if args.download_model:
            try:
                engine.download_model()
                print("[+] Model ready for System-1 decision inference!")
            except Exception as e:
                print(f"[!] Warning: Could not download model ({e}). Internal calibrated engine will be used.")
        else:
            print(f"[!] Warning: Model directory {engine.model_path} not found. Fallback engine will be used.")
    else:
        print(f"[+] Fine-tuned Laya crypto model detected at {engine.model_path} (Device: {engine.device.upper()})!")

    uvicorn.run(
        "src.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )

if __name__ == "__main__":
    main()

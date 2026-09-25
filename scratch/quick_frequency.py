import asyncio
import sys
from pathlib import Path

# Ensure UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

async def quick_frequency_check():
    md = MarketDataService()
    laya = LayaEngine(model_filename="laya_crypto_int8")
    await laya.start_server()
    sig_gen = SignalGenerator()

    # Test top pairs
    symbols = ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"]
    
    # 5m klines: fetch 288 bars (24 hours)
    for sym in symbols:
        klines = await md._fetch_binance_klines(sym, "5m", 288)
        # sample every 12 bars (every 1 hour)
        signals_found = 0
        actions = []
        for i in range(50, len(klines), 6): # every 30 mins
            sub = klines[:i+1]
            calc = compute_all_indicators(sub)
            lat = calc["latest"]
            lat["symbol"] = sym
            lat["interval"] = "5m"
            dec = await laya.decide(lat)
            sig = sig_gen.generate_signal(lat, dec)
            if sig["action"] != "NEUTRAL":
                signals_found += 1
                actions.append(sig["action"])
        print(f"{sym}: found {signals_found} signals across 40 checked 30-min checkpoints ({actions})")
        
    await md.close()
    await laya.close()

if __name__ == "__main__":
    asyncio.run(quick_frequency_check())

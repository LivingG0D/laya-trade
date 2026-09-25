import asyncio
import sys
from pathlib import Path

# Unbuffered stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True, encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

async def measure_daily_frequency():
    md = MarketDataService(exchange="Binance")
    laya = LayaEngine(model_filename="laya_crypto_int8")
    await laya.start_server()
    sig_gen = SignalGenerator()

    symbols = ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P", "1000PEPEUSDT.P"]
    timeframes = ["5m", "15m", "1h"]

    print("=" * 80)
    print(" EMPIRICAL TRADE FREQUENCY MEASUREMENT (24-HOUR ROLLING WINDOW)")
    print("=" * 80)

    for tf in timeframes:
        bars_per_day = 288 if tf == "5m" else (96 if tf == "15m" else 24)
        limit = bars_per_day + 100
        print(f"\n>>> Timeframe: {tf.upper()} ({bars_per_day} candles/day)")
        
        counts = {}
        for sym in symbols:
            # Direct fast Binance klines
            klines = await md._fetch_binance_klines(sym, tf, limit)
            if len(klines) < bars_per_day + 50:
                continue

            trades_in_day = 0
            in_trade_until = 0
            avg_hold_bars = 10 if tf == "5m" else (6 if tf == "15m" else 4)

            start_idx = len(klines) - bars_per_day
            for i in range(start_idx, len(klines)):
                if i < in_trade_until:
                    continue

                sub = klines[:i+1]
                calc = compute_all_indicators(sub)
                lat = calc["latest"]
                lat["symbol"] = sym
                lat["interval"] = tf
                lat["time"] = sub[-1]["time"]

                dec = await laya.decide(lat)
                sig = sig_gen.generate_signal(lat, dec)

                if sig["action"] != "NEUTRAL":
                    trades_in_day += 1
                    in_trade_until = i + avg_hold_bars

            counts[sym] = trades_in_day
            avg_gap = (24.0 / trades_in_day) if trades_in_day > 0 else 24.0
            print(f"  * {sym:14s}: {trades_in_day:2d} trades/day (Avg 1 setup every {avg_gap:.1f} hours)")

        avg_single = sum(counts.values()) / max(1, len(counts))
        print(f"  --> Average per Single Asset: {avg_single:.1f} trades/day")
        print(f"  --> Portfolio (4 Assets):    {sum(counts.values())} trades/day")
        print(f"  --> Portfolio (10 Assets):   ~{int(round(avg_single * 10))} trades/day")

    await md.close()
    await laya.close()

if __name__ == "__main__":
    asyncio.run(measure_daily_frequency())

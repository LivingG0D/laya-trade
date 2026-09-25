import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators

async def scan():
    md = MarketDataService()
    for sym in ['BTCUSDT.P', 'SOLUSDT.P', '1000PEPEUSDT.P']:
        klines = await md.fetch_klines(sym, '15m', limit=500)
        print(f"\n=== {sym} Regimes across 500 15m bars ===")
        for i in range(100, len(klines) - 30, 25):
            sub = klines[:i+1]
            calc = compute_all_indicators(sub)
            lat = calc['latest']
            t_str = datetime.fromtimestamp(sub[-1]['time'], tz=timezone.utc).strftime('%m-%d %H:%M')
            tr = lat['trend_state']
            rsi = lat['rsi']
            sq = lat['is_squeeze']
            adx = lat['adx']
            p = lat['price']
            print(f"idx={i:3d} | {t_str} | Price: {p:10.2f} | Trend: {tr:14s} | RSI: {rsi:5.1f} | ADX: {adx:4.1f} | Sq: {sq}")
    await md.close()

if __name__ == '__main__':
    asyncio.run(scan())

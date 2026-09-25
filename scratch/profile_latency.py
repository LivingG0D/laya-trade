import time
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator
from src.config import INT8_MODEL_DIR

async def profile():
    md = MarketDataService()
    le = LayaEngine()
    sg = SignalGenerator()
    await le.start_server()
    await le._warmup()
    
    symbol = "BTCUSDT.P"
    interval = "5m"
    
    t0 = time.perf_counter()
    raw_klines = await md.fetch_klines(symbol, interval, 200)
    t1 = time.perf_counter()
    
    calc = compute_all_indicators(raw_klines)
    t2 = time.perf_counter()
    
    latest = calc["latest"]
    latest["symbol"] = symbol
    latest["interval"] = interval
    
    htf, depth, funding = await asyncio.gather(
        md.fetch_htf_context(symbol),
        md.fetch_order_book_depth(symbol),
        md.fetch_funding_rate(symbol),
        return_exceptions=True
    )
    t3 = time.perf_counter()
    latest["htf"] = htf
    latest["depth"] = depth
    latest["funding"] = funding
    
    t_inf_start = time.perf_counter()
    dec = await le.decide(latest)
    t_inf_end = time.perf_counter()
    
    sig = sg.generate_signal(latest, dec)
    t_end = time.perf_counter()
    
    inf_latency = dec.get("latency_ms", 0.0)
    print("=" * 60)
    print("LATENCY PROFILING BREAKDOWN (/api/analyze):")
    print("=" * 60)
    print(f"  1. Network: fetch 200 klines (Toobit):        {(t1 - t0)*1000:6.1f} ms")
    print(f"  2. Indicator Engine: compute 12 indicators:   {(t2 - t1)*1000:6.1f} ms")
    print(f"  3. Network: concurrent HTF + Depth + Funding: {(t3 - t2)*1000:6.1f} ms")
    print(f"  4. Laya AI Model Inference:                   {(t_inf_end - t_inf_start)*1000:6.1f} ms (internal: {inf_latency} ms)")
    print(f"  5. Confluence Gatekeeper & Trade Plan:        {(t_end - t_inf_end)*1000:6.1f} ms")
    print("-" * 60)
    print(f"  TOTAL END-TO-END PIPELINE:                    {(t_end - t0)*1000:6.1f} ms")
    print("=" * 60)

asyncio.run(profile())

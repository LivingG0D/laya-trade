import time
import asyncio
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator
import onnxruntime as ort

async def benchmark():
    md = MarketDataService()
    le = LayaEngine()
    
    # Configure 16 threads for test
    num_cpus = os.cpu_count() or 4
    threads = min(num_cpus, 16)
    print(f"Testing with {threads} threads and full 4-way parallel gather...")
    
    await le.start_server()
    if le._ort_session:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        le._ort_session = ort.InferenceSession(
            str(le.model_path / "model_quantized.onnx"),
            sess_options=opts,
            providers=["CPUExecutionProvider"]
        )
    await le._warmup()
    sg = SignalGenerator()
    
    symbol = "BTCUSDT.P"
    interval = "5m"
    
    # Cold test
    t0 = time.perf_counter()
    results = await asyncio.gather(
        md.fetch_klines(symbol=symbol, interval=interval, limit=200),
        md.fetch_htf_context(symbol),
        md.fetch_order_book_depth(symbol),
        md.fetch_funding_rate(symbol),
        return_exceptions=True
    )
    raw_klines, htf_ctx, depth_ctx, funding_ctx = results
    t_net = time.perf_counter()
    
    calc = compute_all_indicators(raw_klines)
    t_ind = time.perf_counter()
    
    latest = calc["latest"]
    latest["symbol"] = symbol
    latest["interval"] = interval
    latest["time"] = calc["series"][-1]["time"] if calc["series"] else 0
    if isinstance(htf_ctx, dict): latest["htf"] = htf_ctx
    if isinstance(depth_ctx, dict): latest["depth"] = depth_ctx
    if isinstance(funding_ctx, dict): latest["funding"] = funding_ctx
    
    dec = await le.decide(latest)
    t_inf = time.perf_counter()
    
    sig = sg.generate_signal(latest, dec)
    t_end = time.perf_counter()
    
    print("=" * 60)
    print("OPTIMIZED COLD REQUEST:")
    print("=" * 60)
    print(f"  Parallel Network Gather (4 endpoints): {(t_net - t0)*1000:6.1f} ms")
    print(f"  Technical Indicators:                  {(t_ind - t_net)*1000:6.1f} ms")
    print(f"  Laya AI INT8 Inference (16 threads):   {(t_inf - t_ind)*1000:6.1f} ms")
    print(f"  Confluence Gating:                     {(t_end - t_inf)*1000:6.1f} ms")
    print("-" * 60)
    print(f"  TOTAL END-TO-END LATENCY:              {(t_end - t0)*1000:6.1f} ms")
    print("=" * 60)
    
    # Warm test (cache hit)
    t0_w = time.perf_counter()
    results_w = await asyncio.gather(
        md.fetch_klines(symbol=symbol, interval=interval, limit=200),
        md.fetch_htf_context(symbol),
        md.fetch_order_book_depth(symbol),
        md.fetch_funding_rate(symbol),
        return_exceptions=True
    )
    raw_klines_w, htf_w, depth_w, fund_w = results_w
    calc_w = compute_all_indicators(raw_klines_w)
    latest_w = calc_w["latest"]
    latest_w["symbol"] = symbol
    latest_w["interval"] = interval
    if isinstance(htf_w, dict): latest_w["htf"] = htf_w
    if isinstance(depth_w, dict): latest_w["depth"] = depth_w
    if isinstance(fund_w, dict): latest_w["funding"] = fund_w
    dec_w = await le.decide(latest_w)
    sig_w = sg.generate_signal(latest_w, dec_w)
    t_end_w = time.perf_counter()
    
    print("\nOPTIMIZED WARM REQUEST (Cache Hit):")
    print(f"  TOTAL END-TO-END LATENCY:              {(t_end_w - t0_w)*1000:6.1f} ms")

asyncio.run(benchmark())

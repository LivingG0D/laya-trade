import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load both datasets
b5 = json.load(open(BASE_DIR / 'scratch' / 'benchmark_results.json'))
b15 = json.load(open(BASE_DIR / 'scratch' / 'comprehensive_benchmark_results.json'))['scenarios']

print("=== INSPECTING TRADES FOR $10 SIMULATION ===")
print("--- 5m Benchmark Trades ---")
for s in b5:
    out = s['outcome']
    sig = s['signal']
    t_str = s['cutoff_time']
    bars = out.get('exit_bar', 1)
    duration_min = bars * 5
    print(f"Time: {t_str} | Sym: {s['symbol']:10s} | Act: {sig['action']} | Res: {out['result']:15s} | Bars: {bars:2d} ({duration_min:3d}m) | ROE: {out['roe_pct']:+6.1f}%")

print("\n--- 15m Benchmark Trades ---")
for s in b15:
    out = s['outcome']
    sig = s['signal']
    if sig['action'] != 'NEUTRAL':
        t_str = s['cutoff_time']
        bars = out.get('exit_bar', 1)
        duration_min = bars * 15
        print(f"Time: {t_str} | Sym: {s['symbol']:10s} | Act: {sig['action']} | Res: {out['result']:15s} | Bars: {bars:2d} ({duration_min:3d}m) | ROE: {out['roe_pct']:+6.1f}%")

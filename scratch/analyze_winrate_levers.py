import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load 5m results
scenarios_5m = json.load(open(BASE_DIR / 'scratch' / 'benchmark_results.json'))

print("=== ANALYZING WIN RATE LEVERS ON 5m BENCHMARK ===")
reached_1r = 0
reached_075r = 0

for s in scenarios_5m:
    entry = s['signal']['entry_price']
    sl = s['signal']['stop_loss']
    risk_pct = abs(entry - sl) / entry * 100
    mfe = s['outcome']['mfe_pct']
    mae = s['outcome']['mae_pct']
    
    hit_1r = mfe >= risk_pct * 1.0
    hit_075r = mfe >= risk_pct * 0.75
    
    if hit_1r: reached_1r += 1
    if hit_075r: reached_075r += 1
    
    name = s['scenario_name'][:32]
    res = s['outcome']['result']
    print(f"{name:32s} | Risk: {risk_pct:5.2f}% | MFE: {mfe:5.2f}% | MAE: {mae:5.2f}% | Hit 1.0R: {str(hit_1r):5s} | Hit 0.75R: {str(hit_075r):5s} | Result: {res}")

print("-" * 75)
print(f"Original Win Rate (TP1 = 1.5R): 3 / 7 (42.9%)")
print(f"If TP1 was 1.0R:                {reached_1r} / 7 ({reached_1r/7*100:.1f}%)")
print(f"If BE Trigger was 0.75R:        {reached_075r} / 7 ({reached_075r/7*100:.1f}% protected from loss)")

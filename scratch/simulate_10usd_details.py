import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load 5m and 15m benchmarks
b5 = json.load(open(BASE_DIR / 'scratch' / 'benchmark_results.json'))
b15 = json.load(open(BASE_DIR / 'scratch' / 'comprehensive_benchmark_results.json'))['scenarios']

def parse_iso(t_str):
    # '2026-09-24 17:50 UTC'
    return datetime.strptime(t_str.replace(" UTC", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

# 1. Prepare 5m intraday sequence (chronological)
trades_5m = []
for s in b5:
    out = s['outcome']
    sig = s['signal']
    if sig['action'] != 'NEUTRAL':
        t_dt = parse_iso(s['cutoff_time'])
        bars = out.get('exit_bar', 1)
        dur_min = bars * 5
        trades_5m.append({
            "name": s['scenario_name'],
            "symbol": s['symbol'],
            "action": sig['action'],
            "start_time": t_dt,
            "cutoff_str": s['cutoff_time'],
            "bars": bars,
            "dur_min": dur_min,
            "entry": sig['entry_price'],
            "exit": out['exit_price'],
            "result": out['result'],
            "spot_pnl": out['pnl_pct'],
            "roe_pct": out['roe_pct'],
            "r_mult": out['r_multiple']
        })

# Sort chronologically
trades_5m.sort(key=lambda x: x["start_time"])

print("=" * 85)
print(" SIMULATION 1: 5-MINUTE INTRADAY ACCELERATION ($10 STARTING CAPITAL, 50x MARGIN)")
print("=" * 85)

balance_compound = 10.00
balance_fixed = 10.00
fixed_margin = 10.00
leverage = 50

print(f"{'#':2s} | {'Time (UTC)':16s} | {'Pair':9s} | {'Side':5s} | {'Entry':10s} | {'Exit':10s} | {'ROE %':7s} | {'PnL ($)':8s} | {'Comp Balance':12s} | {'Dur (min)':9s}")
print("-" * 105)

total_in_trade_minutes = 0
first_start = trades_5m[0]["start_time"]
last_end_minutes = 0

for i, t in enumerate(trades_5m, 1):
    roe_dec = t["roe_pct"] / 100.0
    
    # Compounding equity: entire balance as margin (50x leverage notional = balance * 50)
    pnl_compound = balance_compound * roe_dec
    balance_compound += pnl_compound
    
    # Fixed margin: $10 margin per trade
    pnl_fixed = fixed_margin * roe_dec
    balance_fixed += pnl_fixed

    total_in_trade_minutes += t["dur_min"]
    time_offset = (t["start_time"] - first_start).total_seconds() / 60.0
    end_offset = time_offset + t["dur_min"]
    if end_offset > last_end_minutes:
        last_end_minutes = end_offset

    print(f"{i:2d} | {t['start_time'].strftime('%m-%d %H:%M'):16s} | {t['symbol']:9s} | {t['action']:5s} | {t['entry']:10.2f} | {t['exit']:10.2f} | {t['roe_pct']:+6.1f}% | ${pnl_compound:+7.2f} | ${balance_compound:10.2f}  | {t['dur_min']:3d} mins")

total_wall_clock_hours = last_end_minutes / 60.0
total_in_trade_hours = total_in_trade_minutes / 60.0

print("=" * 85)
print(f"5M INTRADAY RESULTS:")
print(f"  Starting Balance:             $10.00")
print(f"  Ending Balance (Compounded):  ${balance_compound:.2f} (+{((balance_compound - 10) / 10)*100:.1f}% Net Gain, {balance_compound/10:.2f}x)")
print(f"  Ending Balance (Fixed $10):   ${balance_fixed:.2f} (+{((balance_fixed - 10) / 10)*100:.1f}% Net Gain)")
print(f"  Total Wall-Clock Time:        {int(total_wall_clock_hours)} hours {int((total_wall_clock_hours % 1)*60)} minutes (from 11:35 UTC to 19:25 UTC)")
print(f"  Total In-Trade Active Time:   {int(total_in_trade_hours)} hours {int((total_in_trade_hours % 1)*60)} minutes")
print("=" * 85)

# 2. Combined All-Scenario Sequence (Multi-day from Sep 20 to Sep 24)
trades_all = []
for s in b5:
    out = s['outcome']
    sig = s['signal']
    if sig['action'] != 'NEUTRAL':
        t_dt = parse_iso(s['cutoff_time'])
        bars = out.get('exit_bar', 1)
        dur_min = bars * 5
        trades_all.append({
            "tf": "5m",
            "symbol": s['symbol'],
            "action": sig['action'],
            "start_time": t_dt,
            "dur_min": dur_min,
            "roe_pct": out['roe_pct'],
            "result": out['result']
        })

for s in b15:
    out = s['outcome']
    sig = s['signal']
    if sig['action'] != 'NEUTRAL':
        t_dt = parse_iso(s['cutoff_time'])
        bars = out.get('exit_bar', 1)
        dur_min = bars * 15
        trades_all.append({
            "tf": "15m",
            "symbol": s['symbol'],
            "action": sig['action'],
            "start_time": t_dt,
            "dur_min": dur_min,
            "roe_pct": out['roe_pct'],
            "result": out['result']
        })

trades_all.sort(key=lambda x: x["start_time"])

print("\n" + "=" * 85)
print(" SIMULATION 2: COMBINED MULTI-DAY CAMPAIGN (SEP 20 - SEP 24, 9 TRADES)")
print("=" * 85)

bal_all_compound = 10.00
for i, t in enumerate(trades_all, 1):
    roe_dec = t["roe_pct"] / 100.0
    pnl = bal_all_compound * roe_dec
    bal_all_compound += pnl
    print(f"Trade {i}: {t['start_time'].strftime('%Y-%m-%d %H:%M')} | {t['tf']} {t['symbol']:10s} {t['action']:5s} | ROE: {t['roe_pct']:+6.1f}% | PnL: ${pnl:+8.2f} | Balance: ${bal_all_compound:10.2f}")

print("=" * 85)
print(f"COMBINED CAMPAIGN FINAL BALANCE: ${bal_all_compound:.2f} (from initial $10.00)")
print("=" * 85)

import sys
import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any
import httpx

# Ensure UTF-8 output and line buffering
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True, encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
from src.indicators import compute_all_indicators, calculate_ema
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

async def fetch_7days_5m_klines(symbol: str = "BTCUSDT") -> List[Dict[str, Any]]:
    """
    Fetch exactly ~7 days of continuous 5m candles (2,016 candles + 120 warmup = ~2,136 bars).
    Pages backwards from latest candle using Binance Futures REST.
    """
    print(f">>> Fetching 7 days of 5m historical data for {symbol}...")
    url = "https://fapi.binance.com/fapi/v1/klines"
    target_count = 2150
    all_raw = []
    end_time = None

    async with httpx.AsyncClient(timeout=12.0) as client:
        while len(all_raw) < target_count:
            params = {"symbol": symbol, "interval": "5m", "limit": 1000}
            if end_time:
                params["endTime"] = end_time
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_raw = data + all_raw
            end_time = data[0][0] - 1
            if len(data) < 1000:
                break

    # Parse klines into dictionary format
    klines_dict = {}
    for item in all_raw:
        open_time_sec = int(item[0] // 1000)
        klines_dict[open_time_sec] = {
            "time": open_time_sec,
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "is_closed": True
        }

    sorted_klines = [klines_dict[t] for t in sorted(klines_dict.keys())]
    # Slice to last 2150 bars
    if len(sorted_klines) > target_count:
        sorted_klines = sorted_klines[-target_count:]

    t0_str = datetime.fromtimestamp(sorted_klines[0]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    t1_str = datetime.fromtimestamp(sorted_klines[-1]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"    Fetched {len(sorted_klines)} 5m candles spanning from {t0_str} to {t1_str}")
    return sorted_klines

async def run_7day_simulation():
    # 1. Initialize
    symbol = "BTCUSDT.P"
    raw_sym = "BTCUSDT"
    leverage = 50
    initial_balance = 10.00
    warmup_bars = 100

    klines = await fetch_7days_5m_klines(raw_sym)
    total_bars = len(klines)

    print("\n>>> Initializing Fine-Tuned Laya Crypto Model on CUDA GPU...")
    laya = LayaEngine(model_filename="fine_tuned_laya_crypto", device="cuda")
    await laya.start_server()
    sig_gen = SignalGenerator(leverage=leverage)
    print(f">>> Laya Engine Ready on {laya.device.upper()} (GPU).")

    # 2. Simulation State Machine
    balance_compounded = initial_balance
    balance_fixed = initial_balance
    peak_balance = initial_balance
    max_drawdown_pct = 0.0

    in_position = False
    active_trade = None
    trades_ledger = []
    cooldown_until_bar = 0

    print("\n" + "=" * 90)
    print(f" EXECUTING 7-DAY SIMULATION WITH ${initial_balance:.2f} CAPITAL @ {leverage}x MARGIN (5M ONLY)")
    print("=" * 90)

    # Simulation loop across bars
    sim_start_time = time.perf_counter()
    eval_bars = total_bars - warmup_bars

    for i in range(warmup_bars, total_bars):
        current_candle = klines[i]
        c_time = current_candle["time"]
        c_high = current_candle["high"]
        c_low = current_candle["low"]
        c_close = current_candle["close"]

        if (i - warmup_bars) % 250 == 0:
            pct = ((i - warmup_bars) / eval_bars) * 100
            print(f"  ... Simulating Day {1 + int((i - warmup_bars) // 288)}/7: Bar {i - warmup_bars}/{eval_bars} ({pct:.1f}%) | Trades: {len(trades_ledger)} | Balance: ${balance_compounded:.2f} ...", flush=True)

        # CASE 1: In Position -> Check barriers
        if in_position:
            action = active_trade["action"]
            entry = active_trade["entry_price"]
            sl = active_trade["effective_sl"]
            be = active_trade["break_even"]
            tp1 = active_trade["tp1"]
            tp2 = active_trade["tp2"]

            # Excursions
            if action == "LONG":
                bar_mfe = ((c_high - entry) / entry) * 100
                bar_mae = ((entry - c_low) / entry) * 100
            else:
                bar_mfe = ((entry - c_low) / entry) * 100
                bar_mae = ((c_high - entry) / entry) * 100

            active_trade["mfe"] = max(active_trade["mfe"], bar_mfe)
            active_trade["mae"] = max(active_trade["mae"], bar_mae)
            active_trade["bars_held"] += 1

            closed = False
            exit_reason = None
            exit_price = entry

            if action == "LONG":
                # Check BE
                if not active_trade["be_triggered"] and c_high >= be:
                    active_trade["be_triggered"] = True
                    active_trade["effective_sl"] = max(active_trade["effective_sl"], entry)
                    sl = active_trade["effective_sl"]

                # Option A Scalper: Bank profit directly at TP1 (+0.80R)
                if c_high >= tp1:
                    closed = True
                    exit_reason = "TP1_HIT"
                    exit_price = tp1
                # Check SL
                elif c_low <= sl:
                    closed = True
                    exit_price = sl
                    exit_reason = "BE_STOPPED" if (active_trade["be_triggered"] and sl >= entry) else "SL_HIT"

            elif action == "SHORT":
                # Check BE
                if not active_trade["be_triggered"] and c_low <= be:
                    active_trade["be_triggered"] = True
                    active_trade["effective_sl"] = min(active_trade["effective_sl"], entry)
                    sl = active_trade["effective_sl"]

                # Option A Scalper: Bank profit directly at TP1 (+0.80R)
                if c_low <= tp1:
                    closed = True
                    exit_reason = "TP1_HIT"
                    exit_price = tp1
                # Check SL
                elif c_high >= sl:
                    closed = True
                    exit_price = sl
                    exit_reason = "BE_STOPPED" if (active_trade["be_triggered"] and sl <= entry) else "SL_HIT"

            # Max duration safety cutoff (if trade exceeds 36 bars = 3 hours without hitting barriers)
            if not closed and active_trade["bars_held"] >= 36:
                closed = True
                exit_price = c_close
                exit_reason = "TIME_EXPIRED"

            if closed:
                # Calculate PnL and ROE
                risk_dist = abs(entry - active_trade["initial_sl"])
                if exit_reason == "TP1_HIT":
                    result = "SUCCESS_TP1"
                    r_mult = sig_gen.tp1_rr
                    pnl_pct = ((tp1 - entry) / entry * 100) if action == "LONG" else ((entry - tp1) / entry * 100)
                elif exit_reason == "TP2_HIT":
                    result = "SUCCESS_TP2"
                    r_mult = sig_gen.tp2_rr
                    pnl_pct = ((tp2 - entry) / entry * 100) if action == "LONG" else ((entry - tp2) / entry * 100)
                elif active_trade["tp1_triggered"] and exit_reason == "BE_STOPPED":
                    result = "SUCCESS_TP1_BE"
                    r_mult = round(sig_gen.tp1_rr * 0.5, 2)
                    tp1_gain = ((tp1 - entry) / entry * 100) if action == "LONG" else ((entry - tp1) / entry * 100)
                    pnl_pct = tp1_gain * 0.5
                elif exit_reason == "BE_STOPPED":
                    result = "BREAK_EVEN"
                    r_mult = 0.0
                    pnl_pct = 0.0
                elif exit_reason == "SL_HIT":
                    result = "FAILED_SL"
                    r_mult = -1.0
                    loss_dist = abs(active_trade["initial_sl"] - entry)
                    pnl_pct = - (loss_dist / entry * 100)
                else: # TIME_EXPIRED
                    pnl_pct = ((exit_price - entry) / entry * 100) if action == "LONG" else ((entry - exit_price) / entry * 100)
                    r_mult = round(pnl_pct / ((risk_dist / entry) * 100), 2) if risk_dist > 0 else 0.0
                    result = "PARTIAL_WIN" if pnl_pct > 0 else ("PARTIAL_LOSS" if pnl_pct < 0 else "FLAT")

                roe_pct = pnl_pct * leverage

                # Account updates
                pnl_comp = balance_compounded * (roe_pct / 100.0)
                balance_compounded += pnl_comp
                # Prevent negative balance (liquidation protection)
                balance_compounded = max(0.01, balance_compounded)

                pnl_fix = initial_balance * (roe_pct / 100.0)
                balance_fixed += pnl_fix

                # Drawdown tracking
                if balance_compounded > peak_balance:
                    peak_balance = balance_compounded
                current_dd = ((peak_balance - balance_compounded) / peak_balance) * 100
                max_drawdown_pct = max(max_drawdown_pct, current_dd)

                exit_time_str = datetime.fromtimestamp(c_time, tz=timezone.utc).strftime("%m-%d %H:%M")
                dur_minutes = active_trade["bars_held"] * 5

                trade_record = {
                    "id": len(trades_ledger) + 1,
                    "entry_time": active_trade["entry_time_str"],
                    "exit_time": exit_time_str,
                    "action": action,
                    "entry_price": entry,
                    "exit_price": round(exit_price, 2),
                    "result": result,
                    "exit_reason": exit_reason,
                    "roe_pct": round(roe_pct, 1),
                    "pnl_usd": round(pnl_comp, 2),
                    "balance_comp": round(balance_compounded, 2),
                    "balance_fix": round(balance_fixed, 2),
                    "r_mult": r_mult,
                    "bars": active_trade["bars_held"],
                    "dur_min": dur_minutes,
                    "mfe": round(active_trade["mfe"], 2),
                    "mae": round(active_trade["mae"], 2),
                }
                trades_ledger.append(trade_record)
                print(f"  [TRADE #{trade_record['id']:02d}] {trade_record['entry_time']} -> {trade_record['exit_time']} | {trade_record['action']:5s} | {trade_record['result']:14s} | ROE: {trade_record['roe_pct']:+6.1f}% | PnL: ${trade_record['pnl_usd']:+6.2f} | Balance: ${trade_record['balance_comp']:.2f}", flush=True)
                in_position = False
                active_trade = None
                cooldown_until_bar = i + 3 # 3-bar (15-min) cooldown to prevent whipsaw re-entries

        # CASE 2: No Position -> Check for new setup
        elif not in_position:
            if i < cooldown_until_bar:
                continue

            # Sliced 350-bar window provides optimal warmup for EMA200, ATR, RSI, MACD, and 1H HTF trend
            sub = klines[max(0, i - 350) : i + 1]
            calc = compute_all_indicators(sub, include_series=False)
            lat = calc["latest"]
            lat["symbol"] = symbol
            lat["interval"] = "5m"
            lat["time"] = c_time

            # Compute dynamic 1H Higher Timeframe (HTF) trend from 5m data (12 bars = 1 hour)
            closes_htf = np.array([k["close"] for k in sub])
            ema_htf_fast = calculate_ema(closes_htf, min(len(closes_htf), 120)) # ~10h trend
            ema_htf_slow = calculate_ema(closes_htf, min(len(closes_htf), 240)) # ~20h trend
            f_val = ema_htf_fast[-1] if len(ema_htf_fast) and not np.isnan(ema_htf_fast[-1]) else c_close
            s_val = ema_htf_slow[-1] if len(ema_htf_slow) and not np.isnan(ema_htf_slow[-1]) else c_close

            if c_close > f_val > s_val:
                htf_trend = "STRONG_BULLISH"
            elif c_close < f_val < s_val:
                htf_trend = "STRONG_BEARISH"
            elif c_close > f_val:
                htf_trend = "BULLISH"
            elif c_close < f_val:
                htf_trend = "BEARISH"
            else:
                htf_trend = "NEUTRAL"

            lat["htf"] = {"trend": htf_trend}

            laya_dec = await laya.decide(lat)
            sig = sig_gen.generate_signal(lat, laya_dec)

            if sig["action"] in ["LONG", "SHORT"]:
                in_position = True
                entry_time_str = datetime.fromtimestamp(c_time, tz=timezone.utc).strftime("%m-%d %H:%M")
                active_trade = {
                    "action": sig["action"],
                    "entry_price": sig["entry_price"],
                    "initial_sl": sig["stop_loss"],
                    "effective_sl": sig["stop_loss"],
                    "break_even": sig["break_even"],
                    "tp1": sig["take_profit_1"],
                    "tp2": sig["take_profit_2"],
                    "entry_time_str": entry_time_str,
                    "entry_time_ts": c_time,
                    "bars_held": 0,
                    "be_triggered": False,
                    "tp1_triggered": False,
                    "mfe": 0.0,
                    "mae": 0.0
                }

    elapsed_sim = time.perf_counter() - sim_start_time
    print(f"\n[+] Simulation finished in {elapsed_sim:.2f} seconds across {eval_bars} candles (7 days).")

    # 3. Print Trade Ledger
    print("\n" + "=" * 105)
    print(" COMPLETE 7-DAY TRADE-BY-TRADE AUDIT LEDGER ($10 STARTING CAPITAL @ 50x MARGIN)")
    print("=" * 105)
    print(f"{'#':2s} | {'Entry Time':11s} -> {'Exit Time':11s} | {'Side':5s} | {'Entry ($)':9s} | {'Exit ($)':9s} | {'Result':14s} | {'ROE %':7s} | {'PnL ($)':8s} | {'Balance':10s} | {'Duration':8s}")
    print("-" * 105)

    for t in trades_ledger:
        print(f"{t['id']:2d} | {t['entry_time']:11s} -> {t['exit_time']:11s} | {t['action']:5s} | {t['entry_price']:9.2f} | {t['exit_price']:9.2f} | {t['result']:14s} | {t['roe_pct']:+6.1f}% | ${t['pnl_usd']:+7.2f} | ${t['balance_comp']:8.2f} | {t['dur_min']:3d}m")

    # 4. Aggregate Scorecard
    total_trades = len(trades_ledger)
    wins = [t for t in trades_ledger if "SUCCESS" in t["result"] or t["result"] == "PARTIAL_WIN"]
    losses = [t for t in trades_ledger if "FAILED" in t["result"] or t["result"] == "PARTIAL_LOSS"]
    be_trades = [t for t in trades_ledger if t["result"] == "BREAK_EVEN"]

    win_rate = (len(wins) / total_trades * 100) if total_trades else 0.0
    non_losing_rate = ((len(wins) + len(be_trades)) / total_trades * 100) if total_trades else 0.0
    total_roe_sum = sum(t["roe_pct"] for t in trades_ledger)
    gross_gain = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    profit_factor = (gross_gain / gross_loss) if gross_loss > 0 else 99.0
    avg_dur = sum(t["dur_min"] for t in trades_ledger) / total_trades if total_trades else 0.0

    print("\n" + "=" * 80)
    print(" 7-DAY 5M SIMULATION PERFORMANCE SCORECARD")
    print("=" * 80)
    print(f"  Starting Balance:               ${initial_balance:.2f}")
    print(f"  Ending Balance (Compounded):    ${balance_compounded:.2f} ({balance_compounded/initial_balance:.2f}x multiplier)")
    print(f"  Net Compounded Profit ($):      +${balance_compounded - initial_balance:.2f} (+{((balance_compounded - initial_balance)/initial_balance)*100:.1f}%)")
    print(f"  Ending Balance (Fixed $10):     ${balance_fixed:.2f} (+{((balance_fixed - initial_balance)/initial_balance)*100:.1f}%)")
    print(f"  Total Duration:                 7 Full Days (168 hours / 2,016 5m candles)")
    print(f"  Total Trades Executed:          {total_trades} trades ({total_trades / 7.0:.1f} trades/day)")
    print(f"  Winning Trades:                 {len(wins)} ({win_rate:.1f}% Win Rate)")
    print(f"  Break-Even Protected Trades:    {len(be_trades)} (0.00% loss)")
    print(f"  Losing Trades (SL hit):         {len(losses)}")
    print(f"  Non-Losing Rate (Wins + BE):    {non_losing_rate:.1f}%")
    print(f"  Profit Factor:                  {profit_factor:.2f}")
    print(f"  Total Cumulative ROE (Sum):     {total_roe_sum:+.1f}%")
    print(f"  Maximum Drawdown:               -{max_drawdown_pct:.1f}% (from peak equity)")
    print(f"  Average Trade Duration:         {int(avg_dur)} minutes ({avg_dur/60:.1f} hours)")
    print("=" * 80)

    # Save to JSON
    save_path = BASE_DIR / "scratch" / "7day_5m_simulation_results.json"
    scorecard = {
        "initial_balance": initial_balance,
        "ending_balance_compounded": round(balance_compounded, 2),
        "ending_balance_fixed": round(balance_fixed, 2),
        "net_profit_compounded_pct": round(((balance_compounded - initial_balance)/initial_balance)*100, 1),
        "total_days": 7,
        "total_candles": eval_bars,
        "total_trades": total_trades,
        "trades_per_day": round(total_trades / 7.0, 1),
        "wins": len(wins),
        "losses": len(losses),
        "break_even": len(be_trades),
        "win_rate_pct": round(win_rate, 1),
        "non_losing_rate_pct": round(non_losing_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 1),
        "trades": trades_ledger
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2)
    print(f"\n[+] Full 7-day results saved to: {save_path}")

    await laya.close()

if __name__ == "__main__":
    asyncio.run(run_7day_simulation())

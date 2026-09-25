import sys
import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any
import httpx
import numpy as np

# Ensure UTF-8 output and line buffering
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True, encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "models" / "Eikos-4B-INT4"))

from src.indicators import compute_all_indicators, calculate_ema
from src.signal_generator import SignalGenerator
from letter_adapter import LetterAdapter
from decision_core import options_of

CACHE_KLINES_PATH = BASE_DIR / "scratch" / "btc_7day_5m_klines.json"

async def get_7days_5m_klines(symbol: str = "BTCUSDT") -> List[Dict[str, Any]]:
    """Fetch or load cached 7 days of continuous 5m candles."""
    if CACHE_KLINES_PATH.exists():
        print(f">>> Loading cached 7-day 5m candles from {CACHE_KLINES_PATH}...")
        with open(CACHE_KLINES_PATH, "r", encoding="utf-8") as f:
            sorted_klines = json.load(f)
        t0_str = datetime.fromtimestamp(sorted_klines[0]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        t1_str = datetime.fromtimestamp(sorted_klines[-1]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"    Loaded {len(sorted_klines)} 5m candles spanning from {t0_str} to {t1_str}")
        return sorted_klines

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
    if len(sorted_klines) > target_count:
        sorted_klines = sorted_klines[-target_count:]

    with open(CACHE_KLINES_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_klines, f)

    t0_str = datetime.fromtimestamp(sorted_klines[0]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    t1_str = datetime.fromtimestamp(sorted_klines[-1]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"    Fetched and cached {len(sorted_klines)} 5m candles spanning from {t0_str} to {t1_str}")
    return sorted_klines


class EikosEngine:
    def __init__(self, model_dir: Path, device: str = "cuda"):
        print(f">>> Initializing Eikos-4B-INT4 on {device.upper()}...")
        self.adapter = LetterAdapter(str(model_dir), device=device)
        self.adapter.load()
        self.device = device
        self.question = {
            "type": "choice",
            "instructions": "Given the 1H macro trend, 5m momentum, support/resistance, and volume, what is the optimal 5m trade action?",
            "criteria": {
                "STRONG_LONG": "Aggressive Long / buy breakout aligned with bullish momentum",
                "SCALP_LONG": "Quick scalp Long bouncing off support",
                "NEUTRAL_HOLD": "Stay neutral / in cash due to chop, low volume, or conflicting timeframes",
                "SCALP_SHORT": "Quick scalp Short rejecting resistance",
                "STRONG_SHORT": "Aggressive Short / sell breakdown aligned with bearish momentum"
            }
        }
        self.opts = options_of(self.question)
        # Warmup pass
        print(">>> Warming up Eikos model...")
        warmup_state = "Asset: BTCUSDT. Price: 85000. Trend: BULLISH. RSI: 55. ADX: 20."
        self.adapter.dist(warmup_state, self.question, self.opts)
        print(">>> Eikos-4B-INT4 Warmup complete!")

    def decide(self, lat: Dict[str, Any]) -> Dict[str, Any]:
        price = lat["price"]
        rsi = lat["rsi"]
        macd_h = lat.get("macd_hist", 0.0)
        adx = lat.get("adx", 15.0)
        p_di = lat.get("plus_di", 20.0)
        m_di = lat.get("minus_di", 20.0)
        regime = lat.get("market_regime", "CHOP_RANGE")
        t_state = lat.get("trend_state", "NEUTRAL")
        htf = lat.get("htf", {}).get("trend", "NEUTRAL")
        supp = lat.get("support", price * 0.995)
        resis = lat.get("resistance", price * 1.005)
        vol_sp = lat.get("vol_spike", 1.0)
        bb_b = lat.get("bb_pct_b", 0.5)

        state = (
            f"Asset: BTCUSDT (5m candle). Current Price: {price:.2f}.\n"
            f"- 1-Hour Macro Trend: {htf}\n"
            f"- 5m Structure & Regime: {regime} | Trend: {t_state}\n"
            f"- RSI(14): {rsi:.1f} | MACD Hist: {macd_h:+.2f} | ADX: {adx:.1f} (+DI: {p_di:.1f}, -DI: {m_di:.1f})\n"
            f"- Support: {supp:.2f} | Resistance: {resis:.2f}\n"
            f"- Volume Spike: {vol_sp:.2f}x average | BB %B: {bb_b:.2f}"
        )

        t0 = time.perf_counter()
        probs, _ = self.adapter.dist(state, self.question, self.opts)
        lat_ms = (time.perf_counter() - t0) * 1000

        p_sl = probs.get("STRONG_LONG", 0.0)
        p_scl = probs.get("SCALP_LONG", 0.0)
        p_nh = probs.get("NEUTRAL_HOLD", 0.0)
        p_scs = probs.get("SCALP_SHORT", 0.0)
        p_ss = probs.get("STRONG_SHORT", 0.0)

        long_prob = p_sl + p_scl
        short_prob = p_ss + p_scs
        hold_prob = p_nh

        top_choice = max(probs, key=probs.get)
        dir_sum = max(1e-5, long_prob + short_prob)
        dir_conv = max(long_prob, short_prob) / dir_sum

        # Guard: if hold_prob > 0.40 or directional sum is weak, force NEUTRAL_HOLD
        if hold_prob >= 0.45 or (abs(long_prob - short_prob) < 0.10):
            pos_choice = "NEUTRAL_HOLD"
        else:
            pos_choice = top_choice

        return {
            "position": pos_choice,
            "probabilities": {
                "STRONG_LONG": round(p_sl, 4),
                "SCALP_LONG": round(p_scl, 4),
                "NEUTRAL_HOLD": round(p_nh, 4),
                "SCALP_SHORT": round(p_scs, 4),
                "STRONG_SHORT": round(p_ss, 4)
            },
            "long_prob": round(long_prob, 3),
            "short_prob": round(short_prob, 3),
            "hold_prob": round(hold_prob, 3),
            "directional_conviction": round(dir_conv, 4),
            "confidence_score": 4 if dir_conv >= 0.65 else (3 if dir_conv >= 0.55 else 2),
            "confidence_prob": round(dir_conv, 3),
            "squeeze_risk": 0.15,
            "time_horizon": "SCALP",
            "latency_ms": round(lat_ms, 1),
            "engine": "eikos_4b_int4_cuda"
        }


async def run_7day_simulation():
    # 1. Configuration
    symbol = "BTCUSDT.P"
    raw_sym = "BTCUSDT"
    leverage = 50
    initial_balance = 10.00
    warmup_bars = 100

    klines = await get_7days_5m_klines(raw_sym)
    total_bars = len(klines)

    model_dir = BASE_DIR / "models" / "Eikos-4B-INT4"
    eikos = EikosEngine(model_dir=model_dir, device="cuda")
    sig_gen = SignalGenerator(leverage=leverage)

    # 2. State Machine
    balance_compounded = initial_balance
    balance_fixed = initial_balance
    peak_balance = initial_balance
    max_drawdown_pct = 0.0

    in_position = False
    active_trade = None
    trades_ledger = []
    cooldown_until_bar = 0

    print("\n" + "=" * 90)
    print(f" EXECUTING 7-DAY EIKOS-4B-INT4 SIMULATION (${initial_balance:.2f} CAPITAL @ {leverage}x MARGIN, 5M ONLY)")
    print("=" * 90)

    sim_start_time = time.perf_counter()
    eval_bars = total_bars - warmup_bars
    eikos_eval_count = 0
    total_eikos_latency_s = 0.0

    for i in range(warmup_bars, total_bars):
        current_candle = klines[i]
        c_time = current_candle["time"]
        c_high = current_candle["high"]
        c_low = current_candle["low"]
        c_close = current_candle["close"]

        if (i - warmup_bars) % 150 == 0:
            pct = ((i - warmup_bars) / eval_bars) * 100
            print(f"  ... Day {1 + int((i - warmup_bars) // 288)}/7: Bar {i - warmup_bars}/{eval_bars} ({pct:.1f}%) | Trades: {len(trades_ledger)} | Eikos Calls: {eikos_eval_count} | Balance: ${balance_compounded:.2f} ...", flush=True)

        # CASE 1: In Position -> Check barriers
        if in_position:
            action = active_trade["action"]
            entry = active_trade["entry_price"]
            sl = active_trade["effective_sl"]
            be = active_trade["break_even"]
            tp1 = active_trade["tp1"]
            tp2 = active_trade["tp2"]

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

            # Max duration cutoff (36 bars = 3 hours)
            if not closed and active_trade["bars_held"] >= 36:
                closed = True
                exit_price = c_close
                exit_reason = "TIME_EXPIRED"

            if closed:
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

                pnl_comp = balance_compounded * (roe_pct / 100.0)
                balance_compounded += pnl_comp
                balance_compounded = max(0.01, balance_compounded)

                pnl_fix = initial_balance * (roe_pct / 100.0)
                balance_fixed += pnl_fix

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
                cooldown_until_bar = i + 3

        # CASE 2: No Position -> Check for new setup
        elif not in_position:
            if i < cooldown_until_bar:
                continue

            sub = klines[max(0, i - 350) : i + 1]
            calc = compute_all_indicators(sub, include_series=False)
            lat = calc["latest"]
            lat["symbol"] = symbol
            lat["interval"] = "5m"
            lat["time"] = c_time

            # 1H HTF trend computation
            closes_htf = np.array([k["close"] for k in sub])
            ema_htf_fast = calculate_ema(closes_htf, min(len(closes_htf), 120))
            ema_htf_slow = calculate_ema(closes_htf, min(len(closes_htf), 240))
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

            # Fast Pre-Filter for 5m Scalper:
            # If market is completely flat/dead (ADX < 12 and RSI between 48 and 52 and Vol < 0.6x), skip model call
            adx_val = lat.get("adx", 15.0)
            rsi_val = lat.get("rsi", 50.0)
            vol_val = lat.get("vol_spike", 1.0)
            if adx_val < 12.0 and (48.0 <= rsi_val <= 52.0) and vol_val < 0.60:
                continue

            t_eikos_start = time.perf_counter()
            eikos_dec = eikos.decide(lat)
            eikos_eval_count += 1
            total_eikos_latency_s += (time.perf_counter() - t_eikos_start)

            sig = sig_gen.generate_signal(lat, eikos_dec)

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
    avg_eikos_ms = (total_eikos_latency_s / eikos_eval_count * 1000) if eikos_eval_count else 0.0
    print(f"\n[+] Simulation finished in {elapsed_sim:.2f} seconds across {eval_bars} candles (7 days).")
    print(f"[+] Total Eikos Model Evaluations: {eikos_eval_count} (Avg Latency: {avg_eikos_ms:.1f} ms/call)")

    # 3. Print Summary
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
    print(" 7-DAY 5M EIKOS-4B-INT4 SIMULATION SCORECARD")
    print("=" * 80)
    print(f"  Starting Balance:               ${initial_balance:.2f}")
    print(f"  Ending Balance (Compounded):    ${balance_compounded:.2f} ({balance_compounded/initial_balance:.2f}x)")
    print(f"  Net Compounded Profit ($):      +${balance_compounded - initial_balance:.2f} ({((balance_compounded - initial_balance)/initial_balance)*100:+.1f}%)")
    print(f"  Ending Balance (Fixed $10):     ${balance_fixed:.2f} ({((balance_fixed - initial_balance)/initial_balance)*100:+.1f}%)")
    print(f"  Total Duration:                 7 Full Days (2,016 candles)")
    print(f"  Total Trades Executed:          {total_trades} trades ({total_trades / 7.0:.1f} trades/day)")
    print(f"  Winning Trades:                 {len(wins)} ({win_rate:.1f}% Win Rate)")
    print(f"  Break-Even Protected Trades:    {len(be_trades)} (0.00% loss)")
    print(f"  Losing Trades (SL hit):         {len(losses)}")
    print(f"  Non-Losing Rate (Wins + BE):    {non_losing_rate:.1f}%")
    print(f"  Profit Factor:                  {profit_factor:.2f}")
    print(f"  Total Cumulative ROE (Sum):     {total_roe_sum:+.1f}%")
    print(f"  Maximum Drawdown:               -{max_drawdown_pct:.1f}%")
    print(f"  Average Trade Duration:         {int(avg_dur)} minutes ({avg_dur/60:.1f} hours)")
    print("=" * 80)

    # Save Results
    save_path = BASE_DIR / "scratch" / "7day_eikos_simulation_results.json"
    scorecard = {
        "model": "Eikos-4B-INT4",
        "initial_balance": initial_balance,
        "ending_balance_compounded": round(balance_compounded, 2),
        "ending_balance_fixed": round(balance_fixed, 2),
        "net_profit_compounded_pct": round(((balance_compounded - initial_balance)/initial_balance)*100, 1),
        "net_profit_fixed_pct": round(((balance_fixed - initial_balance)/initial_balance)*100, 1),
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
        "eikos_eval_count": eikos_eval_count,
        "avg_latency_ms": round(avg_eikos_ms, 1),
        "trades": trades_ledger
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2)
    print(f"\n[+] Eikos 7-day simulation results saved to: {save_path}")

if __name__ == "__main__":
    asyncio.run(run_7day_simulation())

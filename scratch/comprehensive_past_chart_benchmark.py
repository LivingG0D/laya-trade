import sys
import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

# Ensure UTF-8 console output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

class LayaPastChartBenchmark:
    def __init__(self, model_name: str = "laya_crypto_int8", leverage: int = 50, forward_bars: int = 24):
        self.leverage = leverage
        self.forward_bars = forward_bars
        self.md = MarketDataService()
        self.laya = LayaEngine(model_filename=model_name)
        self.sig_gen = SignalGenerator(leverage=leverage)
        self.results: List[Dict[str, Any]] = []

    async def initialize(self):
        print(">>> Initializing Laya Model...")
        await self.laya.start_server()
        print(f">>> Laya Engine Ready: Device = {self.laya.device if hasattr(self.laya, 'device') else 'CPU'}")

    async def run_scenario(
        self,
        scenario_id: int,
        name: str,
        symbol: str,
        interval: str,
        klines: List[Dict[str, Any]],
        cutoff_idx: int,
        regime_desc: str
    ) -> Dict[str, Any]:
        past_slice = klines[:cutoff_idx + 1]
        future_slice = klines[cutoff_idx + 1 : cutoff_idx + 1 + self.forward_bars]

        cutoff_candle = past_slice[-1]
        cutoff_ts = cutoff_candle["time"]
        cutoff_str = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        cutoff_price = cutoff_candle["close"]

        # Compute technical indicators on past chart
        calc = compute_all_indicators(past_slice)
        latest_telemetry = calc["latest"]
        latest_telemetry["symbol"] = symbol
        latest_telemetry["interval"] = interval
        latest_telemetry["time"] = cutoff_ts

        # Laya Model Decision
        t_start = time.perf_counter()
        laya_decision = await self.laya.decide(latest_telemetry)
        latency_ms = round((time.perf_counter() - t_start) * 1000, 1)

        # Generate Actionable Trade Setup
        signal = self.sig_gen.generate_signal(latest_telemetry, laya_decision)

        action = signal["action"]          # LONG, SHORT, NEUTRAL
        pos_type = signal["signal_type"]   # STRONG_LONG, SCALP_LONG, etc.
        entry_p = signal["entry_price"]
        stop_l = signal["stop_loss"]
        tp1_p = signal["take_profit_1"]
        tp2_p = signal["take_profit_2"]
        be_p = signal["break_even"]
        confidence = signal["confidence_pct"]

        # Evaluate what happened next in future price action
        eval_res = self._evaluate_forward_path(
            action=action,
            entry=entry_p,
            sl=stop_l,
            tp1=tp1_p,
            tp2=tp2_p,
            be=be_p,
            future_candles=future_slice
        )

        scenario_res = {
            "id": scenario_id,
            "name": name,
            "symbol": symbol,
            "interval": interval,
            "regime_type": regime_desc,
            "cutoff_timestamp": cutoff_ts,
            "cutoff_time": cutoff_str,
            "entry_price": cutoff_price,
            "past_bars": len(past_slice),
            "future_bars": len(future_slice),
            "indicators": {
                "rsi": latest_telemetry.get("rsi"),
                "trend": latest_telemetry.get("trend_state"),
                "adx": latest_telemetry.get("adx"),
                "macd_hist": latest_telemetry.get("macd_hist"),
                "atr": latest_telemetry.get("atr"),
                "support": latest_telemetry.get("support"),
                "resistance": latest_telemetry.get("resistance"),
                "is_squeeze": latest_telemetry.get("is_squeeze"),
            },
            "laya": {
                "position": pos_type,
                "probabilities": laya_decision.get("probabilities", {}),
                "long_prob": laya_decision.get("long_prob"),
                "short_prob": laya_decision.get("short_prob"),
                "hold_prob": laya_decision.get("hold_prob"),
                "confidence_prob": laya_decision.get("confidence_prob"),
                "engine": laya_decision.get("engine"),
                "latency_ms": latency_ms
            },
            "signal": {
                "action": action,
                "entry_price": entry_p,
                "stop_loss": stop_l,
                "tp1": tp1_p,
                "tp2": tp2_p,
                "break_even": be_p,
                "confidence_pct": confidence,
                "reasons": signal.get("reasons", [])
            },
            "outcome": eval_res
        }
        self.results.append(scenario_res)
        return scenario_res

    def _evaluate_forward_path(
        self,
        action: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        be: float,
        future_candles: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not future_candles:
            return {"status": "NO_DATA", "result": "UNKNOWN"}

        if action == "NEUTRAL":
            highs = [c["high"] for c in future_candles]
            lows = [c["low"] for c in future_candles]
            max_p = max(highs)
            min_p = min(lows)
            range_pct = ((max_p - min_p) / entry) * 100
            is_choppy = range_pct < 1.5
            return {
                "status": "COMPLETED",
                "result": "SUCCESS_AVOIDED_CHOP" if is_choppy else "MISSED_RUNAWAY",
                "is_success": is_choppy,
                "exit_reason": "CAPITAL_PRESERVED" if is_choppy else "CHOP_EXPANDED",
                "pnl_pct": 0.0,
                "roe_pct": 0.0,
                "r_multiple": 0.0,
                "mfe_pct": round(((max_p - entry) / entry) * 100, 2),
                "mae_pct": round(((entry - min_p) / entry) * 100, 2),
                "total_range_pct": round(range_pct, 2),
                "details": f"Price drifted in a {range_pct:.2f}% range ({'Noise avoided' if is_choppy else 'Trend occurred without entry'})"
            }

        be_triggered = False
        tp1_triggered = False
        tp2_triggered = False
        sl_triggered = False
        effective_sl = sl

        mfe_pct = 0.0
        mae_pct = 0.0
        exit_price = entry
        exit_bar = len(future_candles)
        exit_reason = "EXPIRED"

        for idx, bar in enumerate(future_candles, 1):
            h = bar["high"]
            l = bar["low"]

            if action == "LONG":
                bar_mfe = ((h - entry) / entry) * 100
                bar_mae = ((entry - l) / entry) * 100
                mfe_pct = max(mfe_pct, bar_mfe)
                mae_pct = max(mae_pct, bar_mae)

                # Check BE
                if not be_triggered and h >= be:
                    be_triggered = True
                    effective_sl = max(effective_sl, entry)

                # Check TP1
                if not tp1_triggered and h >= tp1:
                    tp1_triggered = True

                # Check TP2
                if tp1_triggered and h >= tp2:
                    tp2_triggered = True
                    exit_price = tp2
                    exit_bar = idx
                    exit_reason = "TP2_HIT"
                    break

                # Check SL
                if l <= effective_sl:
                    sl_triggered = True
                    exit_price = effective_sl
                    exit_bar = idx
                    exit_reason = "BE_STOPPED" if (be_triggered and effective_sl >= entry) else "SL_HIT"
                    break

            elif action == "SHORT":
                bar_mfe = ((entry - l) / entry) * 100
                bar_mae = ((h - entry) / entry) * 100
                mfe_pct = max(mfe_pct, bar_mfe)
                mae_pct = max(mae_pct, bar_mae)

                # Check BE
                if not be_triggered and l <= be:
                    be_triggered = True
                    effective_sl = min(effective_sl, entry)

                # Check TP1
                if not tp1_triggered and l <= tp1:
                    tp1_triggered = True

                # Check TP2
                if tp1_triggered and l <= tp2:
                    tp2_triggered = True
                    exit_price = tp2
                    exit_bar = idx
                    exit_reason = "TP2_HIT"
                    break

                # Check SL
                if h >= effective_sl:
                    sl_triggered = True
                    exit_price = effective_sl
                    exit_bar = idx
                    exit_reason = "BE_STOPPED" if (be_triggered and effective_sl <= entry) else "SL_HIT"
                    break

        risk_dist = abs(entry - sl)
        if exit_reason == "TP2_HIT":
            result = "SUCCESS_TP2"
            pnl_pct = ((tp2 - entry) / entry * 100) if action == "LONG" else ((entry - tp2) / entry * 100)
            r_mult = self.sig_gen.tp2_rr
        elif tp1_triggered and exit_reason == "BE_STOPPED":
            result = "SUCCESS_TP1_BE"
            tp1_gain = ((tp1 - entry) / entry * 100) if action == "LONG" else ((entry - tp1) / entry * 100)
            pnl_pct = tp1_gain * 0.5
            r_mult = round(self.sig_gen.tp1_rr * 0.5, 2)
        elif tp1_triggered and not sl_triggered:
            result = "SUCCESS_TP1"
            pnl_pct = ((tp1 - entry) / entry * 100) if action == "LONG" else ((entry - tp1) / entry * 100)
            r_mult = self.sig_gen.tp1_rr
        elif exit_reason == "BE_STOPPED":
            result = "BREAK_EVEN"
            pnl_pct = 0.0
            r_mult = 0.0
        elif exit_reason == "SL_HIT":
            result = "FAILED_SL"
            loss_dist = abs(sl - entry)
            pnl_pct = - (loss_dist / entry * 100)
            r_mult = -1.0
        else: # EXPIRED at window end
            last_c = future_candles[-1]["close"]
            exit_price = last_c
            pnl_pct = ((last_c - entry) / entry * 100) if action == "LONG" else ((entry - last_c) / entry * 100)
            r_mult = round(pnl_pct / ((risk_dist / entry) * 100), 2) if risk_dist > 0 else 0.0
            result = "PARTIAL_WIN" if pnl_pct > 0 else ("PARTIAL_LOSS" if pnl_pct < 0 else "FLAT")

        roe_pct = pnl_pct * self.leverage

        return {
            "status": "COMPLETED",
            "result": result,
            "is_success": "SUCCESS" in result or result == "PARTIAL_WIN",
            "exit_reason": exit_reason,
            "exit_price": round(exit_price, 4),
            "exit_bar": exit_bar,
            "pnl_pct": round(pnl_pct, 2),
            "roe_pct": round(roe_pct, 1),
            "r_multiple": round(r_mult, 2),
            "mfe_pct": round(mfe_pct, 2),
            "mae_pct": round(mae_pct, 2),
            "tp1_reached": tp1_triggered,
            "tp2_reached": tp2_triggered,
            "be_triggered": be_triggered,
            "sl_hit": sl_triggered
        }

    async def close(self):
        await self.md.close()
        await self.laya.close()

async def main():
    benchmark = LayaPastChartBenchmark(leverage=50, forward_bars=24)
    await benchmark.initialize()

    print("\n" + "=" * 80)
    print(" FETCHING DIVERSE MULTI-DAY MARKET DATA (500 bars / 15m intervals)")
    print("=" * 80)

    symbols = ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P", "1000PEPEUSDT.P"]
    market_data = {}
    for s in symbols:
        klines = await benchmark.md.fetch_klines(s, interval="15m", limit=500)
        market_data[s] = klines
        t0 = datetime.fromtimestamp(klines[0]['time'], tz=timezone.utc).strftime("%m-%d %H:%M")
        t1 = datetime.fromtimestamp(klines[-1]['time'], tz=timezone.utc).strftime("%m-%d %H:%M")
        print(f"  [+] {s:15s} : {len(klines)} bars from {t0} to {t1} UTC")

    # Curate 8 diverse historical chart test cases
    scenarios = [
        # Scenario 1: BTC Strong Bearish Breakdown
        {
            "id": 1,
            "name": "BTC Flash Breakdown & Support Loss",
            "symbol": "BTCUSDT.P",
            "interval": "15m",
            "cutoff_idx": 375,
            "regime": "Strong Bearish Breakdown (RSI 32, ADX 48.6)"
        },
        # Scenario 2: BTC Bullish Momentum Expansion
        {
            "id": 2,
            "name": "BTC Upward Momentum Breakout",
            "symbol": "BTCUSDT.P",
            "interval": "15m",
            "cutoff_idx": 175,
            "regime": "Strong Bullish Expansion (RSI 72.9, ADX 43.2)"
        },
        # Scenario 3: BTC Volatility Squeeze & Chop
        {
            "id": 3,
            "name": "BTC Low-Volatility Squeeze Compression",
            "symbol": "BTCUSDT.P",
            "interval": "15m",
            "cutoff_idx": 325,
            "regime": "TTM Squeeze Active + Low ADX Chop (ADX 9.8)"
        },
        # Scenario 4: SOL Severe Trend Breakdown
        {
            "id": 4,
            "name": "SOL Bearish Momentum & Rejection",
            "symbol": "SOLUSDT.P",
            "interval": "15m",
            "cutoff_idx": 250,
            "regime": "Bearish Trend Rejection (RSI 34.4, ADX 29.6)"
        },
        # Scenario 5: SOL Pre-Breakout Squeeze
        {
            "id": 5,
            "name": "SOL Coiling Range Before Major Leg",
            "symbol": "SOLUSDT.P",
            "interval": "15m",
            "cutoff_idx": 100,
            "regime": "Tight Range Compression (ADX 14.9, Squeeze True)"
        },
        # Scenario 6: SOL Bullish Wave Continuation
        {
            "id": 6,
            "name": "SOL Bullish Continuation at New Highs",
            "symbol": "SOLUSDT.P",
            "interval": "15m",
            "cutoff_idx": 225,
            "regime": "Strong Bullish Uptrend Continuation (ADX 39.1)"
        },
        # Scenario 7: PEPE Extreme Capitulation Dump
        {
            "id": 7,
            "name": "PEPE High-Beta Meme Capitulation Dump",
            "symbol": "1000PEPEUSDT.P",
            "interval": "15m",
            "cutoff_idx": 400,
            "regime": "Extreme Oversold Dump (RSI 28.1, ADX 70.1)"
        },
        # Scenario 8: PEPE High-Velocity Parabolic Breakout
        {
            "id": 8,
            "name": "PEPE Meme Parabolic Breakout",
            "symbol": "1000PEPEUSDT.P",
            "interval": "15m",
            "cutoff_idx": 200,
            "regime": "Parabolic Expansion (RSI 72.1, ADX 63.6)"
        }
    ]

    print("\n" + "=" * 80)
    print(f" RUNNING BENCHMARK SIMULATION ACROSS {len(scenarios)} HISTORICAL CHARTS")
    print("=" * 80)

    for sc in scenarios:
        kl = market_data[sc["symbol"]]
        res = await benchmark.run_scenario(
            scenario_id=sc["id"],
            name=sc["name"],
            symbol=sc["symbol"],
            interval=sc["interval"],
            klines=kl,
            cutoff_idx=sc["cutoff_idx"],
            regime_desc=sc["regime"]
        )

        ind = res["indicators"]
        sig = res["signal"]
        l_dec = res["laya"]
        out = res["outcome"]

        print(f"\n[{res['id']}/8] {res['name']} ({res['symbol']} - {res['cutoff_time']})")
        print(f"  Regime:       {res['regime_type']}")
        print(f"  Past Price:   ${res['entry_price']} | RSI: {ind['rsi']} | Trend: {ind['trend']} | Squeeze: {ind['is_squeeze']}")
        print(f"  Laya AI:      {l_dec['position']} (Latency: {l_dec['latency_ms']}ms, Prob: {l_dec['confidence_prob']})")
        print(f"  Signal Setup: {sig['action']} (Conviction: {sig['confidence_pct']}%)")
        if sig["action"] != "NEUTRAL":
            print(f"    Targets:    Entry: ${sig['entry_price']} | SL: ${sig['stop_loss']} | BE: ${sig['break_even']} | TP1: ${sig['tp1']} | TP2: ${sig['tp2']}")
        print(f"  Outcome:      {out['result']} (Reason: {out.get('exit_reason')} at Bar {out.get('exit_bar')})")
        if sig["action"] != "NEUTRAL":
            print(f"    PnL:        {out['pnl_pct']:+.2f}% | 50x ROE: {out['roe_pct']:+.1f}% | R-Multiple: {out['r_multiple']:+.2f}R")
            print(f"    Excursions: MFE: +{out['mfe_pct']:.2f}% | MAE: -{out['mae_pct']:.2f}%")
        else:
            print(f"    Details:    {out.get('details')}")

    await benchmark.close()

    # Calculate overall aggregate benchmark statistics
    results = benchmark.results
    total_sims = len(results)
    directional = [r for r in results if r["signal"]["action"] != "NEUTRAL"]
    neutrals = [r for r in results if r["signal"]["action"] == "NEUTRAL"]
    
    wins = [r for r in directional if r["outcome"]["is_success"]]
    losses = [r for r in directional if not r["outcome"]["is_success"] and r["outcome"]["result"] != "BREAK_EVEN"]
    be_trades = [r for r in directional if r["outcome"]["result"] == "BREAK_EVEN"]

    win_rate = (len(wins) / len(directional) * 100) if directional else 0.0
    total_pnl = sum(r["outcome"]["pnl_pct"] for r in directional)
    total_roe = sum(r["outcome"]["roe_pct"] for r in directional)
    gross_win = sum(r["outcome"]["pnl_pct"] for r in wins)
    gross_loss = abs(sum(r["outcome"]["pnl_pct"] for r in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 1.0)
    avg_r = sum(r["outcome"]["r_multiple"] for r in directional) / len(directional) if directional else 0.0

    print("\n" + "=" * 80)
    print(" LAYA BENCHMARK PERFORMANCE SCORECARD")
    print("=" * 80)
    print(f"  Total Historical Simulations:    {total_sims}")
    print(f"  Actionable Directional Signals:  {len(directional)}")
    print(f"  Neutral / Capital Preserved:     {len(neutrals)}")
    print(f"  Winning Trades (TP1 / TP2):      {len(wins)} ({win_rate:.1f}% Win Rate)")
    print(f"  Losing Trades (SL Hit):          {len(losses)}")
    print(f"  Break-Even Protected Trades:     {len(be_trades)}")
    print(f"  Total Net Spot PnL:              {total_pnl:+.2f}%")
    print(f"  Total Net 50x Margin ROE:        {total_roe:+.1f}%")
    print(f"  Profit Factor:                   {profit_factor:.2f}")
    print(f"  Average Realized R-Multiple:     {avg_r:+.2f}R")
    print("=" * 80)

    # Save comprehensive results JSON
    save_path = BASE_DIR / "scratch" / "comprehensive_benchmark_results.json"
    scorecard = {
        "total_simulations": total_sims,
        "directional_signals": len(directional),
        "neutral_signals": len(neutrals),
        "wins": len(wins),
        "losses": len(losses),
        "break_even": len(be_trades),
        "win_rate_pct": round(win_rate, 1),
        "net_pnl_pct": round(total_pnl, 2),
        "net_roe_50x_pct": round(total_roe, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_r_multiple": round(avg_r, 2),
        "scenarios": results
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2)
    print(f"\n[+] Saved complete benchmark results & scorecard to: {save_path}")

if __name__ == "__main__":
    asyncio.run(main())

import sys
import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

class ChartBenchmarkRunner:
    def __init__(self, leverage: int = 50, forward_bars: int = 24):
        self.leverage = leverage
        self.forward_bars = forward_bars  # 24 bars of 5m = 2.0 hours evaluation window
        self.md = MarketDataService()
        self.laya = LayaEngine()
        self.sig_gen = SignalGenerator(leverage=leverage)
        self.results = []

    async def initialize(self):
        print(">>> Initializing Laya Decision Engine...")
        started = await self.laya.start_server()
        print(f">>> Laya Engine Status: Ready (Engine: {self.laya.device if hasattr(self.laya, 'device') else 'CPU'})")

    async def run_single_simulation(
        self,
        scenario_name: str,
        symbol: str,
        interval: str,
        klines: List[Dict[str, Any]],
        cutoff_idx: int,
        description: str
    ) -> Dict[str, Any]:
        """
        Runs one historical simulation:
        - Visible past chart: klines up to cutoff_idx
        - Hidden future: klines from cutoff_idx to cutoff_idx + forward_bars
        """
        # 1. Past chart slice visible to Laya
        past_slice = klines[:cutoff_idx + 1]
        future_slice = klines[cutoff_idx + 1 : cutoff_idx + 1 + self.forward_bars]

        cutoff_candle = past_slice[-1]
        cutoff_time_ts = cutoff_candle["time"]
        cutoff_time_str = datetime.fromtimestamp(cutoff_time_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        cutoff_price = cutoff_candle["close"]

        # 2. Compute technical indicators on past chart
        calc = compute_all_indicators(past_slice)
        latest_telemetry = calc["latest"]
        latest_telemetry["symbol"] = symbol
        latest_telemetry["interval"] = interval
        latest_telemetry["time"] = cutoff_time_ts

        # 3. Ask Laya for decision
        laya_decision = await self.laya.decide(latest_telemetry)

        # 4. Generate trade setup
        signal = self.sig_gen.generate_signal(latest_telemetry, laya_decision)

        action = signal["action"]          # LONG, SHORT, or NEUTRAL
        pos_type = signal["signal_type"]   # STRONG_LONG, SCALP_LONG, NEUTRAL_HOLD, etc.
        entry_price = signal["entry_price"]
        stop_loss = signal["stop_loss"]
        tp1 = signal["take_profit_1"]
        tp2 = signal["take_profit_2"]
        be_price = signal["break_even"]
        confidence = signal["confidence_pct"]

        # 5. Evaluate what happened next on that chart
        eval_result = self._evaluate_future_price_action(
            action=action,
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            be_price=be_price,
            future_candles=future_slice
        )

        sim_summary = {
            "scenario_name": scenario_name,
            "description": description,
            "symbol": symbol,
            "interval": interval,
            "cutoff_time": cutoff_time_str,
            "cutoff_price": cutoff_price,
            "past_bars_count": len(past_slice),
            "future_bars_count": len(future_slice),
            "indicators": {
                "rsi": latest_telemetry.get("rsi"),
                "trend_state": latest_telemetry.get("trend_state"),
                "adx": latest_telemetry.get("adx"),
                "support": latest_telemetry.get("support"),
                "resistance": latest_telemetry.get("resistance"),
                "is_squeeze": latest_telemetry.get("is_squeeze"),
            },
            "laya_decision": {
                "position": pos_type,
                "probabilities": laya_decision.get("probabilities", {}),
                "confidence_prob": laya_decision.get("confidence_prob"),
                "squeeze_risk": laya_decision.get("squeeze_risk"),
                "engine": laya_decision.get("engine"),
                "latency_ms": laya_decision.get("latency_ms"),
            },
            "signal": {
                "action": action,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "tp1": tp1,
                "tp2": tp2,
                "break_even": be_price,
                "confidence_pct": confidence,
                "reasons": signal.get("reasons", [])[:3]
            },
            "outcome": eval_result
        }
        self.results.append(sim_summary)
        return sim_summary

    def _evaluate_future_price_action(
        self,
        action: str,
        entry_price: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        be_price: float,
        future_candles: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Step through future candles candle-by-candle to see what happened first:
        - TP1 hit?
        - TP2 hit?
        - SL hit?
        - BE triggered?
        - Max gain (MFE) / Max drawdown (MAE)
        """
        if not future_candles:
            return {"status": "NO_FUTURE_DATA", "result": "UNKNOWN"}

        if action == "NEUTRAL":
            # For NEUTRAL signals, evaluate if market was choppy (successful capital preservation)
            # or if it made a big runaway trend
            highs = [c["high"] for c in future_candles]
            lows = [c["low"] for c in future_candles]
            max_p = max(highs)
            min_p = min(lows)
            max_drift_up_pct = ((max_p - entry_price) / entry_price) * 100
            max_drift_down_pct = ((entry_price - min_p) / entry_price) * 100
            total_range_pct = ((max_p - min_p) / entry_price) * 100

            is_choppy = total_range_pct < 1.2
            return {
                "status": "HOLD_EVALUATED",
                "result": "SUCCESS_AVOIDED_CHOP" if is_choppy else "MISSED_EXPANSION",
                "pnl_pct": 0.0,
                "roe_pct": 0.0,
                "r_multiple": 0.0,
                "max_drift_up_pct": round(max_drift_up_pct, 2),
                "max_drift_down_pct": round(max_drift_down_pct, 2),
                "total_range_pct": round(total_range_pct, 2),
                "details": f"Market moved in a {total_range_pct:.2f}% range ({'Choppy range - capital preserved' if is_choppy else 'Expanded outside chop'})"
            }

        be_triggered = False
        tp1_triggered = False
        tp2_triggered = False
        sl_triggered = False
        effective_sl = stop_loss

        mfe_pct = 0.0  # Max Favorable Excursion (% max profit)
        mae_pct = 0.0  # Max Adverse Excursion (% max drawdown)
        exit_price = entry_price
        exit_bar = len(future_candles)
        exit_reason = "EXPIRED"

        for idx, candle in enumerate(future_candles, 1):
            h = candle["high"]
            l = candle["low"]
            c = candle["close"]

            if action == "LONG":
                # Current bar excursions
                bar_favorable = ((h - entry_price) / entry_price) * 100
                bar_adverse = ((entry_price - l) / entry_price) * 100
                mfe_pct = max(mfe_pct, bar_favorable)
                mae_pct = max(mae_pct, bar_adverse)

                # Check Break-Even trigger
                if not be_triggered and h >= be_price:
                    be_triggered = True
                    effective_sl = max(effective_sl, entry_price)  # Protected at break-even

                # Check Take Profit 1
                if not tp1_triggered and h >= tp1:
                    tp1_triggered = True

                # Check Take Profit 2
                if tp1_triggered and h >= tp2:
                    tp2_triggered = True
                    exit_price = tp2
                    exit_bar = idx
                    exit_reason = "TP2_HIT"
                    break

                # Check Stop Loss
                if l <= effective_sl:
                    sl_triggered = True
                    exit_price = effective_sl
                    exit_bar = idx
                    exit_reason = "BE_STOPPED" if (be_triggered and effective_sl >= entry_price) else "SL_HIT"
                    break

            elif action == "SHORT":
                # Current bar excursions
                bar_favorable = ((entry_price - l) / entry_price) * 100
                bar_adverse = ((h - entry_price) / entry_price) * 100
                mfe_pct = max(mfe_pct, bar_favorable)
                mae_pct = max(mae_pct, bar_adverse)

                # Check Break-Even trigger
                if not be_triggered and l <= be_price:
                    be_triggered = True
                    effective_sl = min(effective_sl, entry_price)  # Protected at break-even

                # Check Take Profit 1
                if not tp1_triggered and l <= tp1:
                    tp1_triggered = True

                # Check Take Profit 2
                if tp1_triggered and l <= tp2:
                    tp2_triggered = True
                    exit_price = tp2
                    exit_bar = idx
                    exit_reason = "TP2_HIT"
                    break

                # Check Stop Loss
                if h >= effective_sl:
                    sl_triggered = True
                    exit_price = effective_sl
                    exit_bar = idx
                    exit_reason = "BE_STOPPED" if (be_triggered and effective_sl <= entry_price) else "SL_HIT"
                    break

        # Calculate final results
        risk_dist = abs(entry_price - stop_loss)
        if exit_reason == "TP2_HIT":
            result = "SUCCESS_TP2"
            pnl_pct = ((tp2 - entry_price) / entry_price * 100) if action == "LONG" else ((entry_price - tp2) / entry_price * 100)
            r_multiple = self.sig_gen.tp2_rr
        elif tp1_triggered and exit_reason == "BE_STOPPED":
            result = "SUCCESS_TP1_BE"
            # 50% closed at TP1, 50% stopped at BE
            tp1_gain = ((tp1 - entry_price) / entry_price * 100) if action == "LONG" else ((entry_price - tp1) / entry_price * 100)
            pnl_pct = tp1_gain * 0.5
            r_multiple = round(self.sig_gen.tp1_rr * 0.5, 2)
        elif tp1_triggered and not sl_triggered:
            result = "SUCCESS_TP1"
            pnl_pct = ((tp1 - entry_price) / entry_price * 100) if action == "LONG" else ((entry_price - tp1) / entry_price * 100)
            r_multiple = self.sig_gen.tp1_rr
        elif exit_reason == "BE_STOPPED":
            result = "BREAK_EVEN"
            pnl_pct = 0.0
            r_multiple = 0.0
        elif exit_reason == "SL_HIT":
            result = "FAILED_SL"
            loss_dist = abs(stop_loss - entry_price)
            pnl_pct = - (loss_dist / entry_price * 100)
            r_multiple = -1.0
        else: # EXPIRED at window end
            last_c = future_candles[-1]["close"]
            exit_price = last_c
            pnl_pct = ((last_c - entry_price) / entry_price * 100) if action == "LONG" else ((entry_price - last_c) / entry_price * 100)
            r_multiple = round(pnl_pct / ((risk_dist / entry_price) * 100), 2) if risk_dist > 0 else 0.0
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
            "r_multiple": round(r_multiple, 2),
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
    runner = ChartBenchmarkRunner(leverage=50, forward_bars=24)
    await runner.initialize()

    print("\n" + "=" * 75)
    print(" FETCHING DIVERSE HISTORICAL MARKET DATA FOR BENCHMARK SIMULATION")
    print("=" * 75)

    # We will fetch historical data for BTC, ETH, and SOL
    symbols = ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"]
    historical_data = {}

    for sym in symbols:
        print(f"Fetching 300 klines for {sym} (5m timeframe)...")
        klines = await runner.md.fetch_klines(sym, interval="5m", limit=300)
        historical_data[sym] = klines
        print(f"  -> Got {len(klines)} klines for {sym} (from {datetime.fromtimestamp(klines[0]['time'], tz=timezone.utc).strftime('%m-%d %H:%M')} to {datetime.fromtimestamp(klines[-1]['time'], tz=timezone.utc).strftime('%m-%d %H:%M')})")

    # Define diverse simulation cutoffs across time and symbols
    # Each scenario takes a cutoff in the past (leaving at least 24 bars in the future)
    simulations_to_run = [
        # BTC Scenarios
        {
            "name": "BTC Simulation 1: Recent Consolidation / Setup",
            "symbol": "BTCUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["BTCUSDT.P"]) - 25,
            "desc": "Past chart leading to 2 hours ago on Bitcoin"
        },
        {
            "name": "BTC Simulation 2: Intermediate Swing Point",
            "symbol": "BTCUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["BTCUSDT.P"]) - 60,
            "desc": "Past chart at intermediate pivot (~5 hours ago)"
        },
        {
            "name": "BTC Simulation 3: Earlier Volatility Regime",
            "symbol": "BTCUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["BTCUSDT.P"]) - 100,
            "desc": "Past chart at structural level (~8.5 hours ago)"
        },
        # ETH Scenarios
        {
            "name": "ETH Simulation 1: Recent Ethereum Microstructure",
            "symbol": "ETHUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["ETHUSDT.P"]) - 25,
            "desc": "Past chart on Ethereum 2 hours ago"
        },
        {
            "name": "ETH Simulation 2: Earlier Trend / Momentum Phase",
            "symbol": "ETHUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["ETHUSDT.P"]) - 70,
            "desc": "Past chart on Ethereum ~6 hours ago"
        },
        # SOL Scenarios
        {
            "name": "SOL Simulation 1: High-Beta Solana Setup",
            "symbol": "SOLUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["SOLUSDT.P"]) - 30,
            "desc": "Past chart on Solana 2.5 hours ago"
        },
        {
            "name": "SOL Simulation 2: Structural Support/Resistance Test",
            "symbol": "SOLUSDT.P",
            "interval": "5m",
            "cutoff_idx": len(historical_data["SOLUSDT.P"]) - 85,
            "desc": "Past chart on Solana ~7 hours ago"
        }
    ]

    print("\n" + "=" * 75)
    print(f" EXECUTING {len(simulations_to_run)} TEST BENCHMARK SIMULATIONS WITH LAYA")
    print("=" * 75)

    for i, sim in enumerate(simulations_to_run, 1):
        sym = sim["symbol"]
        klines = historical_data[sym]
        cutoff = sim["cutoff_idx"]
        
        print(f"\n--- [Simulation {i}/{len(simulations_to_run)}: {sim['name']}] ---")
        res = await runner.run_single_simulation(
            scenario_name=sim["name"],
            symbol=sym,
            interval=sim["interval"],
            klines=klines,
            cutoff_idx=cutoff,
            description=sim["desc"]
        )

        ind = res["indicators"]
        sig = res["signal"]
        l_dec = res["laya_decision"]
        out = res["outcome"]

        print(f"  Chart Cutoff: {res['cutoff_time']} | Price: ${res['cutoff_price']}")
        print(f"  Indicators:   RSI={ind['rsi']} | Trend={ind['trend_state']} | ADX={ind['adx']} | Squeeze={ind['is_squeeze']}")
        print(f"  Laya AI:      {l_dec['position']} (Lat: {l_dec['latency_ms']}ms, Prob: {l_dec['confidence_prob']})")
        print(f"  Trade Signal: {sig['action']} (Conviction: {sig['confidence_pct']}%)")
        if sig["action"] != "NEUTRAL":
            print(f"    Entry: ${sig['entry_price']} | SL: ${sig['stop_loss']} | BE: ${sig['break_even']} | TP1: ${sig['tp1']} | TP2: ${sig['tp2']}")
        print(f"  What Happened Next:")
        print(f"    Result:       {out['result']} (Exit: {out.get('exit_reason', 'N/A')} at Bar {out.get('exit_bar', 'N/A')})")
        if sig["action"] != "NEUTRAL":
            print(f"    PnL:          {out['pnl_pct']:+.2f}% (ROE @ 50x: {out['roe_pct']:+.1f}%) | R-Multiple: {out['r_multiple']:+.2f}R")
            print(f"    Excursions:   MFE (Max Gain): +{out['mfe_pct']:.2f}% | MAE (Max Drawdown): -{out['mae_pct']:.2f}%")
        else:
            print(f"    Details:      {out.get('details', '')}")

    await runner.close()

    # Calculate overall aggregate benchmark statistics
    results = runner.results
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

    print("\n" + "=" * 75)
    print(" LAYA 5M BENCHMARK SCORECARD")
    print("=" * 75)
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
    print("=" * 75)

    # Save benchmark results to JSON file
    out_file = BASE_DIR / "scratch" / "benchmark_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(runner.results, f, indent=2)
    print(f"\n[+] Detailed benchmark results saved to {out_file}")

if __name__ == "__main__":
    asyncio.run(main())

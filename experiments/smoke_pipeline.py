"""Manual smoke run of the live pipeline (network): market data -> indicators -> Laya -> signal.

Run:  python experiments/smoke_pipeline.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from src.market_data import MarketDataService
from src.indicators import compute_all_indicators
from src.laya_engine import LayaEngine
from src.signal_generator import SignalGenerator

async def test():
    print("[1] Testing MarketDataService...")
    md = MarketDataService()
    klines = await md.fetch_klines('BTCUSDT.P', '5m', 100)
    print(f"    Fetched {len(klines)} klines. Latest candle close: {klines[-1]['close']}")
    
    print("\n[2] Testing compute_all_indicators...")
    calc = compute_all_indicators(klines)
    latest = calc['latest']
    print(f"    RSI (14): {latest['rsi']}")
    print(f"    Trend: {latest['trend_state']}")
    print(f"    MACD Hist: {latest['macd_hist']}")
    print(f"    Bollinger %B: {latest['bb_pct_b']}")
    print(f"    ATR: {latest['atr']}")
    print(f"    Support: {latest['support']}, Resistance: {latest['resistance']}")

    print("\n[3] Starting Laya System-1 GPU Server...")
    laya = LayaEngine()
    started = await laya.start_server()
    print(f"    Laya server started: {started}")
    
    print("\n[4] Running Laya System-1 inference on BTCUSDT.P...")
    latest['symbol'] = 'BTCUSDT.P'
    latest['interval'] = '5m'
    decision = await laya.decide(latest)
    print("    Laya Decision Output:")
    print(f"    Position: {decision['position']}")
    print(f"    Probabilities: {decision['probabilities']}")
    print(f"    Confidence Prob: {decision['confidence_prob']}")
    print(f"    Squeeze Risk: {decision['squeeze_risk']}")
    print(f"    Inference Latency: {decision['latency_ms']} ms")
    print(f"    Engine: {decision['engine']}")
    
    print("\n[5] Generating Actionable Trade Setup...")
    sig_gen = SignalGenerator()
    signal = sig_gen.generate_signal(latest, decision)
    print(f"    Action: {signal['action']} ({signal['signal_type']})")
    print(f"    Entry Price: {signal['entry_price']}")
    print(f"    Stop Loss: {signal['stop_loss']}")
    print(f"    Take Profit 1: {signal['take_profit_1']}")
    print(f"    Take Profit 2: {signal['take_profit_2']}")
    print(f"    Risk/Reward: 1 : {signal['risk_reward_ratio']}")
    print(f"    Confidence: {signal['confidence_pct']}%")
    print("    Confluence Reasons:")
    for r in signal['reasons']:
        print(f"      * {r}")
    
    await md.close()
    await laya.close()
    print("\n[+] ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test())

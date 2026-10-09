# 🚀 Laya Crypto TradingView Chart Analyzer & Toobit AI Position Manager

A live crypto market analyzer powered by the **Fine-Tuned Laya Crypto Model** (`models/fine_tuned_laya_crypto`), featuring real-time Toobit Perpetual Futures market data streaming, multi-timeframe technical indicator analysis, System-1 calibrated probabilistic decision-making for Long and Short positions, an interactive TradingView Lightweight Charts WebUI, and direct **Toobit API integration** to manage open positions and receive live AI suggestions.

Default asset: **`BTCUSDT.P`** (Toobit contract `BTC-SWAP-USDT`, with instant switching across 32 perpetual pairs).

---

## ⚡ Highlights

- **Fine-Tuned Laya Crypto Decision Engine**: Leverages `models/fine_tuned_laya_crypto` (ModernBERT-large RL agent fine-tuned on multi-timeframe crypto datasets) for ultra-fast (~45ms) System-1 calibrated probabilistic decisions on local NVIDIA GPU (CUDA) or CPU.
- **Toobit Futures API & Position Management**:
  - Connect your Toobit API key/secret with HMAC SHA-256 request authentication.
  - Live account balance, available margin, and active position telemetry.
  - **1-Click Flash Close**: Immediately closes open positions at market price and clears pending stop orders.
  - **Zero-Risk Demo Mode**: Built-in simulated account with $5,000 virtual USDT margin to test positions and AI suggestions risk-free.
- **Real-Time AI Position Suggestions**:
  - Synthesizes open position telemetry (Side, Entry, Live PnL, Liquidation threshold) with Laya's probabilistic model.
  - Automatically recommends: `KEEP HOLDING 🟢`, `PROTECT PROFIT 🛡️` (trail SL to break-even), `TAKE PROFIT 🎯`, or `EXIT / REDUCE RISK 🚨`.
- **Dual UI Modes**:
  - **🌱 Simple Mode (Beginner)**: Pure, clean chart view with an easy-to-read "Your Active Trade" card, 3-step action plan (Entry, TP, SL), estimated profit calculator, and plain-English AI explanations.
  - **⚡ Pro Mode (Advanced)**: Full technical telemetry (EMA Ribbon, Bollinger Bands, RSI, MACD, Volume Spikes, Pivot Gauge), calibrated probability bars, and detailed positions table.
- **Clean TradingView Lightweight Chart**: Candlestick series, isolated bottom volume scale, and deduplicated on-chart signal markers.
- **Real-Time WebSocket Stream**: Live tick-by-tick Toobit futures streaming (`wss://stream.toobit.com/quote/ws/v1`) with Binance fallback.

---

## 🛠️ Architecture

```
laya-trade/
├── src/
│   ├── config.py              # Configuration, exchange endpoints, cost and risk defaults
│   ├── market_data.py         # Toobit & Binance Futures REST / WebSocket client
│   ├── market_snapshot.py     # Closed-candle decision state (auto-trader, dashboard, backtest)
│   ├── indicators.py          # Vectorized technical indicator suite (EMA, RSI, MACD, BB, ATR)
│   ├── laya_engine.py         # Laya model runtimes (ONNX / native / fallback) and decisions
│   ├── signal_generator.py    # Decision -> signal: trade plan plus display fields
│   ├── trade_plan.py          # The trade plan every signal carries, and its one parser
│   ├── trade_management.py    # Exit rules for open positions, shared by live, demo and backtest
│   ├── exchange.py            # The exchange seam (interface + quantity rounding)
│   ├── toobit_client.py       # Live adapter: Toobit HMAC SHA-256 signed API
│   ├── simulated_exchange.py  # Demo adapter: virtual account marked to live prices
│   ├── auto_trader.py         # Autonomous trading supervisor and circuit breakers
│   ├── position_advice.py     # Plain-language suggestions for the position card
│   ├── strategy_evaluator.py  # Backtest evaluator and cost model
│   ├── edge_gate.py           # Outcome-based trade gate (edge tables)
│   └── app.py                 # FastAPI backend, Toobit REST routes & WebSockets
├── experiments/               # Research scripts; harness.py is their shared evaluation
├── web/                       # Modern Dark-Themed WebUI
├── tests/                     # pytest suite (run: python -m pytest)
├── main.py                    # Application launcher
├── requirements.txt           # Python dependencies
└── GLOSSARY.md                # Domain terms used across the code
```

---

## 🚀 Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Analyzer
```bash
python main.py
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser!

### Optional CLI Arguments
```bash
# Custom port or symbol
python main.py --symbol 1000PEPEUSDT.P --port 8000

# Download model GGUF explicitly
python main.py --download-model
```

---

## 🔒 Security & Privacy

- **Local Processing**: API keys and secrets are processed strictly in memory on your local machine and never transmitted to third parties or logged.
- **Permission Best Practice**: When creating an API key on Toobit, only enable **Contract / Futures Trade** permissions. **Never enable 'Withdrawal' permissions**.
- **Model Downloads**: Model weights (`*.gguf`) are downloaded automatically from Hugging Face on demand and kept in the local `models/` directory, excluded from git tracking.

---

## 🧠 How Laya Analyzes the Market

Laya evaluates market state in a single forward pass:

1. **Position Recommendation (`choice`)**:
   - `STRONG_LONG`: Clear upward trend, price above key EMAs, healthy RSI, expanding volume.
   - `SCALP_LONG`: Support bounce, oversold reversal, slight upward tilt.
   - `NEUTRAL_HOLD`: Choppy range, conflicting indicators, high risk of false breakout.
   - `SCALP_SHORT`: Resistance rejection, overbought pullback.
   - `STRONG_SHORT`: Clear breakdown, price below key EMAs, heavy bearish volume.
2. **Confidence (`score`)**: Calibrated probability rating from 1 to 4.
3. **Squeeze Risk (`noul`)**: Imminent risk of violent volatility squeeze or whipsaw.
4. **Position AI Advisory**: Evaluates current position alignment against market momentum to recommend trailing stops, taking profit, or cutting losses.

---

## 📜 License
Apache-2.0
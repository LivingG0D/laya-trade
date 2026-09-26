# Phase 2: Learned Entry Signal (Gradient Boosting) and Laya Probe

## Goal

Replace hand-written entry rules with a model trained on outcomes, and find
out whether the existing Laya model has any edge when queried the way it was
trained. The Phase 0 backtest harness decides pass or fail. "No edge found" is
an acceptable, reported outcome; nothing goes live without passing.

## Part A: Laya probe

The Laya fine-tune (`X:\laya btc datasets\prepare_full_mtf_laya.py`,
`train_laya.py`) used 15m BTC bars, a JSON state (15m RSI, Supertrend(10,3)
direction and distance, 30m/1h/2h/4h Supertrend trends, bull-count, 1m RSI,
1m volume surge) and one question: LONG / SHORT / NO_TRADE for +1.2% before
-0.6% within 2 hours. Live queries it differently (5m bars, free text, a
5-option question), which explains its near-uniform output.

`experiments/laya_probe.py` runs the native model on the held-out validation
file (`laya_mtf_val.jsonl`, 35,388 rows, 2025-09 to 2026-09) with the exact
training state and question, then reports ROC AUC of P(LONG) against LONG and
P(SHORT) against SHORT, and the hit rate of the top-probability decile. AUC
near 0.5 means no information. The model's temperature was fitted on this
file, which does not affect AUC.

## Part B: Gradient-boosting signal

- **Data:** 365 days of 5m, 15m, 1h, 4h Binance futures candles for BTC, ETH,
  SOL, BNB, XRP, DOGE, SUI, AVAX, LINK, LTC (existing paged, cached loader).
- **Labels** (`src/barrier.py`): for each 5m bar, entry at the next open; long
  wins if +1.2% is hit before -0.6% within 24 bars (short mirrored). A bar that
  touches both counts as a loss. Unresolved at the horizon is a loss.
- **Features** (`src/features.py`, shared by training and live): from closed
  candles only. 5m: log returns over 1/3/12/48 bars, realized volatility over
  12/48, ATR%, RSI, MACD histogram / price, Bollinger %B, ADX, DI difference,
  distance to EMA 9/21/50/200, position in and distance to the 20-bar range,
  volume z-score, taker-buy ratio, squeeze flag, hour of day (sin/cos).
  15m/1h/4h: RSI, 1- and 4-bar returns, distance to EMA 21/50, ADX, ATR%,
  aligned so a higher-timeframe candle counts only once it has closed at or
  before the 5m bar's close. Reuses `src/indicators.py`.
- **Model** (`src/gbm_signal.py`): two `HistGradientBoostingClassifier`s (long,
  short) trained on all symbols pooled; isotonic calibration fitted on the
  last 20% of each training span; save/load with joblib.
- **Walk-forward:** first 120 days train only; then retrain every 30 days on
  all earlier data, with a 24-bar purge before each test window. The last 60
  days are a holdout, predicted once by a model trained on everything before
  it (purged), and evaluated last.
- **Decision rule (fixed, not tuned):** break-even win rate for this bracket
  after 0.12% costs is (0.6 + 0.12) / 1.8 = 40%. Trade the better side when its
  calibrated probability is at least 45%.
- **Evaluation:** AUC and calibration per side; signals replayed through
  `evaluate_signal_sequence` with the barrier bracket, 2-hour time stop and
  costs; random entries at the same trade count and flat as baselines; per
  30-day window and on the holdout.
- **Pass criteria (all):** holdout expectancy > 0R after costs pooled across
  symbols, on at least 30 holdout trades; at least 60% of all walk-forward
  windows positive (a window with no trades counts as not positive); beats
  random by more than the full round-trip cost (0.12% = 0.2R, slippage
  included).

## Part C: Live wiring (only if Part B passes)

A `signal_engine` setting (`laya` default, `gbm`) and an engine that builds
features from the last 1000 closed candles per timeframe and loads the saved
model. Shadow mode stays on. sklearn goes into requirements.txt.

## Tests

Barrier labels (TP first, SL first, same-bar conflict, horizon, shorts).
Feature causality (appending future bars never changes past rows), HTF
alignment (no unclosed candle), 1000-bar live window matches full-history
features within tolerance. Model fit/predict on synthetic separable data,
calibrated output monotonic, save/load round trip. Walk-forward window purge.
Probe record conversion.

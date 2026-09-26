# Phase 3: 15-Minute Strategies

## Goal

Find a 15m strategy that is profitable after costs out of sample, or show that
none of three well-motivated candidates is. Nothing goes live without passing.

## What Phase 2 taught

OHLCV features predict volatility (AUC 0.70-0.75) but not direction (0.51).
Fixed 0.6% stops make costs 20% of risk. So Phase 3 sizes brackets by ATR,
takes direction from the higher-timeframe trend or a breakout, and tests a
cheaper maker-entry cost scenario.

## Data

365 days of 15m candles for the Phase 2 symbols, plus 1h, 4h and 1d context
(30 extra warmup days), from the cached Binance loader (`1d` interval added).
Features come from `build_features` generalised to any base timeframe and
context set (15m base; 1h, 4h, 1d context with prefixes h1, h4, d1).

## Strategies (parameters fixed in advance, none tuned on results)

- **A. Trend pullback.** Trend up when the latest closed daily candle is above
  its EMA21 and the 4h candle above its EMA50 (`d1_ema_dist_21 > 0`,
  `h4_ema_dist_50 > 0`); trend down mirrored. Long when trend is up and the
  15m RSI crosses back above 40 (previous bar below 40); short when trend is
  down and RSI crosses back below 60. Stop 1.5 x ATR, target 3 x ATR, 16-bar
  (4h) time stop.
- **C. Gradient boosting at 15m.** Labels: +2 x ATR before -1 x ATR within 16
  bars, entry at the next open, per side (ATR from the signal bar). Trade the
  better side when expected R after costs is at least +0.1R:
  `3p - 1 - cost_r >= 0.1`, with `cost_r` = round-trip cost / (ATR / price).
  Stop 1 x ATR, target 2 x ATR, 16-bar time stop.
- **B. Volatility breakout.** Volatility score = C's `p_long + p_short`. Long
  when the 15m close breaks above the previous 20 bars' high, short below
  their low, only when the score is in the top 20% of scores on that
  window's training rows. Same bracket as C.

## Costs

- Taker: 4 bps fee + 2 bps slippage per side (as before). Decides pass/fail.
- Maker entry: entry 2 bps fee, no slippage; exit taker. Reported alongside.
  The evaluator's `CostModel` gains optional entry-side fee and slippage.

## Evaluation

Same walk-forward as Phase 2 (120 days train, 30-day windows, 60-day holdout,
24-hour label purge for C/B), same pass checks: holdout expectancy > 0 on at
least 30 trades, at least 60% of windows positive, and beating random entries
(same bracket, matched trade count) by more than the full round-trip cost in
R. Each trade records its initial stop distance so that cost is exact per
trade.

## Live wiring

Only for a strategy that passes, in shadow mode, as a separate step.

## Tests

ATR-scaled barrier labels; `build_features` with 15m base and 1d context
(causality, alignment); CostModel entry overrides; strategy A trigger logic;
EV threshold for C; breakout trigger for B; random baseline with per-bar
brackets.

# Phase 4: Higher-Timeframe Trend Following

## Goal

Phases 0-3 found no gross edge in candle-only strategies at 5m-15m. Trend
following on slow bars (hold days to weeks) is the one candle-only approach
with long published evidence, and costs are a small share of each move. Test
it honestly: is a classic trend rule profitable after costs and funding, out
of sample, and better than random entries with the same exits?

## Data

Binance USDT-M futures, 10 Phase 2 symbols, daily and 4h candles from each
symbol's listing (up to 2600 days back), plus each symbol's full funding-rate
history (`/fapi/v1/fundingRate`, cached in scratch/history).

## Rule (fixed in advance, not tuned)

Donchian 55/20, the Turtle "System 2" shape:

- Long when the close is above the highest high of the previous 55 bars;
  short when below the lowest low of the previous 55 bars.
- Exit a long when the close is below the lowest low of the previous 20 bars;
  exit a short when above the highest high of the previous 20 bars.
- Initial stop 2 x ATR(20) from entry, fixed for the trade (defines 1R).
- Signals use the closed bar; entries and channel exits fill at the next
  open. Stops fill intrabar, at the open when price gaps through.
- Exit and opposite entry on the same bar = reverse.

Run on daily bars (primary) and 4h bars (secondary). Both decide pass/fail;
a long-only daily variant is reported for information only.

## Costs

Taker 4 bps fee + 2 bps slippage per side, plus actual funding: each 8h
funding event while a position is open charges `side * rate * price` (longs
pay positive funding).

## Evaluation

No training, so no purge. Each symbol is simulated over its whole history once;
trades are bucketed by entry time. 180-day walk-forward windows, the last 365
days are the holdout. Pass checks as before: holdout expectancy > 0 on at least
30 trades, at least 60% of windows positive, and beating random entries (random
side, placed only on flat bars, same exits, matched trade count, 20 seeds) by
more than the full round-trip cost in R.

Also reported: yearly R, max drawdown in R (trades summed in exit order at 1%
risk each), and buy-and-hold per symbol. The 10 symbols move together, so the
effective sample is smaller than the trade count suggests.

## Live wiring

Only for a rule that passes, in shadow mode, as a separate step.

## Tests

Donchian signals use only previous bars; simulator: next-open entry, stop
fill with gap, channel exit at next open, reversal, funding sign; random
entries only on flat bars.

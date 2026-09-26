# Backtest Harness Design

## Goal

Replace the broken `experiments/strategy_baseline.py` with a backtest that
measures the system the auto-trader actually runs, over months of history,
against simple baselines. Phase 0 of the profitability plan: measure before
changing any strategy logic.

## Problems with the current script

- Series rows have no `price`, so every signal has entry 0 and stop 0; the
  evaluator rejects all of them. The script can produce no trades.
- `LayaEngine()` is never started, so only the rule-based fallback runs.
- No higher-timeframe context, 150 bars of history, no baselines.

## Design

1. **History** (`experiments/history.py`): Binance futures `fapi/v1/klines`
   paged backwards by `endTime` (1500 bars per call). Toobit's endpoint returns
   nothing when given `endTime`, so it cannot serve history. Pages are
   deduplicated and sorted with `market_data._parse_klines`; the result is
   cached at `scratch/history/{symbol}_{interval}.json` and a re-run fetches
   only the missing tail. Candles that have not closed are dropped.
2. **Shared state builder** (`src/auto_trader.py`): `timeframe_snapshot()` and
   `merge_timeframes()` are pulled out of `AutoTrader._analyze_symbol` so live
   and backtest build identical states. The backtest feeds each 5m bar the last
   150 closed candles per timeframe; 15m/1h candles count only once their close
   time is at or before the 5m bar's close (no look-ahead).
3. **Engines** (`src/laya_engine.py`): the native branch of `decide()` is split
   into `_agent_decision()` (model only) and `_blend()` (50/50 with fallback).
   The backtest compares `live` (blend), `model`, and `fallback` on the same
   bars. Model outputs are computed with `predict_batch` and cached per input
   text hash in `scratch/backtest_cache/`.
4. **Baselines**, all through `SignalGenerator` (same bracket and filters):
   `flat`, `random` (same directional rate as `live`, 5 seeds), `ema_cross`
   (EMA 9/21 cross).
5. **Evaluator** (`src/strategy_evaluator.py`): two opt-in rules that mirror
   live exits: break-even move (stop to entry +0.05% once price moves 9% ROE at
   the configured leverage, or TP1 fills) and opposing-signal exit (at the next
   open when an opposite signal reaches the confidence threshold). Each trade
   records `cost_r` (fees / risk).
6. **Report**: per symbol × strategy — trades, win %, expectancy R, profit
   factor, max drawdown, average cost R — for the full period and for 14-day
   walk-forward test windows. JSON to `scratch/backtests/`, table to stdout.

Defaults: 90 days, BTC/ETH/SOL, 5m entries with 15m + 1h context, 4 bps fee +
2 bps slippage per side, min confidence 65, leverage 50 for the ROE trigger.

## Known gaps (printed with the report)

Binance prices stand in for Toobit; funding is 0; sizing is fixed-risk while
live uses fixed margin (results are compared in R); no order-book history
(the live auto-trader does not use it either).

## Tests

History page stitching and tail caching (httpx `MockTransport`); HTF alignment
has no look-ahead; break-even and opposing exits; seeded random baseline is
deterministic; shared builder matches the auto-trader's previous output;
existing suite stays green.

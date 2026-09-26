# Phase 1: Exits, Costs and Outcome-Based Gating

## Goal

Stop the measured leaks in the auto-trader: break-even exits that lose money
after fees, a bracket that does not match what the model predicts, trades
whose costs eat most of the risk, and a "confidence" gate with no measured
link to outcomes. Every change is measured with `experiments/strategy_baseline.py`
against the 90-day baseline (live: about -0.23R per trade) and random entries.

## Changes

1. **Break-even fix.** The stop moves to break-even only after TP1 fills, and
   the new stop is entry plus round-trip costs (0.12%), so a break-even exit no
   longer loses money. The 9%-ROE trigger is removed. Evaluator:
   `breakeven_after_tp1`; live: `breakeven_mode = "tp1_fees"`.
2. **Barrier bracket.** Exit profile `barrier`: TP +1.2%, SL -0.6%, one target,
   close after 2 hours: the event the fine-tuned model was trained on.
   SignalGenerator `exit_profile`; signals carry `max_hold_seconds`; the
   evaluator closes at the bar close once the hold time is reached (`TIME`);
   live closes the position with a market order.
3. **Cost filter.** SignalGenerator rejects a trade when round-trip costs are
   more than 20% of its risk (`max_cost_share`).
4. **Outcome-based gate.** Every directional signal gets the R it would have
   produced if taken alone. A table of mean R by (side, 5-point confidence
   bucket), fitted only on signals whose outcome window ends before the test
   window starts, keeps a signal only when its bucket's mean R is positive
   with at least 30 samples. The same gate replaces the 70% opposing exit. The
   table is saved as JSON; live loads it when `edge_gate` is on.
5. **Risk sizing (live).** `sizing_mode = "risk_pct"`: margin sized so a stop-out
   loses `equity_risk_pct` of equity at the configured leverage; this is what
   the evaluator already does.

## Defaults and safety

All live changes are settings that default to current behaviour until the
backtest shows the new behaviour is better; the auto-trader stays in shadow
mode. Old behaviour remains available.

## Tests

Evaluator: break-even after TP1 with cost offset, time stop, gap handling kept.
SignalGenerator: barrier bracket prices and hold time, cost filter. Gate:
purged walk-forward fit uses no outcome that overlaps the test window; bucket
lookup and minimum sample size. AutoTrader: risk sizing margin, time stop
close, break-even mode, gated candidates and opposing exit.

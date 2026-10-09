# Glossary

Domain terms as the code uses them. Module names in brackets.

**Trade plan** [`src/trade_plan.py`] — what a signal asks trading to do: side, planned entry, stop, TP1, TP2 (0 when TP1 closes everything), confidence and maximum holding time. Every producer emits these keys and every consumer reads them through `TradePlan.from_signal`; a neutral signal has no plan.

**Signal** — a trade plan plus display fields for the web UI (colours, marker, reason text, ROE strings). Python code never reads the display fields back.

**Bracket** — the protective orders of an open position: a stop on the whole size, TP1 on half and TP2 on the rest (or TP1 on the whole size when there is no TP2). A position that arrives without one gets the **fallback bracket** (0.25 / 0.40 / 0.60 return on margin).

**Trade management** [`src/trade_management.py`] — the rules for an open position beyond its bracket fill: break-even, time stop, opposing exit. The live auto-trader, the simulated exchange and the backtest evaluator all run these functions; callers own only the price source and how an action executes.

**Exit policy** — the trade-management settings: break-even mode, leverage, opposing-exit threshold. Built from the auto-trader's settings with `ExitPolicy.from_settings`; the backtest builds the same one.

**Break-even** — moving the stop to entry plus an offset once a position has earned it. `legacy`: TP1 reached or a 9% return on margin, stop at entry + 0.05%. `tp1_fees`: TP1 reached only, stop at entry + round-trip costs. `off`.

**Time stop** — a market close once a position has been held for its plan's maximum holding time.

**Opposing exit** — a market close when an opposite plan is confident enough (70%), or when the edge gate allows it.

**Closed candle** — a candle whose open time plus its interval is at or before now. Decisions only ever see closed candles; the still-forming bar is for charts.

**Market snapshot** [`src/market_snapshot.py`] — the closed-candle decision state: indicator telemetry per timeframe merged into the entry timeframe, with higher-timeframe context (the 1h trend, else 15m). Built the same way by the auto-trader, the dashboard and the backtest.

**Exchange** [`src/exchange.py`] — the seam between trading and a venue. Two adapters: the **live exchange** (`ToobitClient`, signed HTTP) and the **simulated exchange** (`SimulatedExchange`, the demo account marked to live prices, brackets filled with the trade-management rule).

**Close path** — `AutoTrader.close_trade`: the one way the auto-trader and the app close a position (time stops, opposing exits, emergency and manual closes), booking it toward the daily-loss and consecutive-loss breakers. Bracket fills on the exchange are booked when the position disappears.

**Cost model** [`CostModel` in `src/strategy_evaluator.py`] — fee and slippage per side in basis points, defaults from `src/config.py`; `round_trip` is the full entry-plus-exit cost as a price fraction.

**R** — a trade's result as a multiple of the amount it risked (entry to initial stop).

**Edge gate / edge table** [`src/edge_gate.py`] — trades only signal buckets (side × 5-point confidence) whose measured mean R after costs is positive with enough samples; tables are fit by the backtest and read by the auto-trader.

**Research harness** [`experiments/harness.py`] — the evaluation every phase script runs its rule through: walk-forward windows and holdout, label purging, matched random baseline, pass checks, report lines.

**Random baseline** — random entries with the rule's own exits, placed only where the rule could trade and rescaled until they produce the rule's trade count.

**Walk-forward / holdout** — chronological test windows after an initial training span; the holdout is the final span, evaluated once.

**Shadow mode** — the auto-trader scans and logs its candidate but submits no orders.

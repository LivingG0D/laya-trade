# Backtest Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A backtest that runs the live decision path over 90 days of history and compares it with baselines.

**Architecture:** Reuse live code (indicators, auto-trader state building, engine, signal generator, evaluator). New code is only the history fetcher, the per-bar replay loop, baselines, and two evaluator exit rules.

**Tech Stack:** Python 3.12, httpx, numpy, pytest, laya (FP8 on CUDA).

**Spec:** `docs/superpowers/specs/2026-09-26-backtest-harness-design.md`

## Global Constraints

- No look-ahead: a signal at bar i trades at bar i+1 open; HTF candles count only after they close.
- Live behaviour unchanged: existing tests stay green; refactors are extract-only.
- Evaluator additions are opt-in (defaults keep current results).
- Generated data lives under `scratch/` (git-ignored).

---

### Task 1: Evaluator exit rules
**Files:** Modify `src/strategy_evaluator.py`; Test `tests/test_strategy_evaluator.py`
- [ ] Failing tests: break-even trigger moves stop to entry×1.0005 for following bars; TP1 fill moves stop to break-even; opposing signal ≥ threshold exits at next open with reason `OPPOSING`; trades carry `cost_r`.
- [ ] Add `EvaluationConfig.breakeven_trigger_pct`, `breakeven_offset_pct`, `opposing_exit_confidence`; implement; run suite; commit.

### Task 2: Shared state builder
**Files:** Modify `src/auto_trader.py`; Test `tests/test_backtest_state.py`
- [ ] Failing test: `merge_timeframes()` of `timeframe_snapshot()` outputs equals the state `_analyze_symbol` passes to the engine.
- [ ] Extract `timeframe_snapshot(raw, symbol, timeframe, leverage)` and `merge_timeframes(latest_by_tf)`; `_analyze_symbol` calls them; run suite; commit.

### Task 3: Engine split
**Files:** Modify `src/laya_engine.py`; Test `tests/test_backtest_state.py`
- [ ] Failing test: `_blend(agent_res, fallback_res)` reproduces `decide()` output with a fake agent.
- [ ] Extract `_agent_summary`, `_agent_decision`, `_blend`; run suite; commit.

### Task 4: History fetcher
**Files:** Create `experiments/history.py`; Test `tests/test_history.py`
- [ ] Failing tests (httpx MockTransport): pages stitched in order without duplicates down to the start time; unclosed last candle dropped; cached file extended by tail fetch only.
- [ ] Implement `fetch_range()` and `load_history()`; commit.

### Task 5: Replay, baselines, report
**Files:** Rewrite `experiments/strategy_baseline.py`; Test `tests/test_backtest_state.py`
- [ ] Failing tests: `htf_window()` excludes 1h candles whose close is after the bar close; `random_signals()` deterministic per seed; `ema_cross_decisions()` fires only on crosses.
- [ ] Implement replay (states per bar, cached HTF snapshots), engines, baselines, walk-forward windows, report; commit.

### Task 6: Run and verify
- [ ] `python experiments/strategy_baseline.py --engines fallback --days 7` smoke run; then full default run; record results; `pytest -q` green; commit.

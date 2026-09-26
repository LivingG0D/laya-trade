# Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix break-even, bracket, cost and gating leaks; prove each with the backtest.

**Architecture:** Evaluator and SignalGenerator gain opt-in behaviour; a new `src/edge_gate.py` holds the outcome table; AutoTrader reads new settings that default to current behaviour.

**Tech Stack:** Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-phase1-exits-design.md`

## Global Constraints

- Round-trip cost 0.12% (4 bps fee + 2 bps slippage per side) is the break-even offset and cost-filter basis.
- Barrier bracket: TP 1.2%, SL 0.6%, max hold 7200 s.
- Gate: bucket = (side, floor(confidence/5)*5); keep when mean R > 0 and n >= 30; fit only on outcomes ending before the test window.
- Live defaults unchanged; shadow mode stays on.

---

### Task 1: Break-even after TP1 with cost offset
- [ ] Tests: evaluator `breakeven_after_tp1` moves stop to entry×(1+0.0012) after TP1 only; price trigger independent.
- [ ] Implement; backtest `--breakeven tp1_fees`; record; commit.

### Task 2: Barrier bracket and time stop
- [ ] Tests: generator barrier prices, `max_hold_seconds`; evaluator `TIME` exit at close.
- [ ] Implement; backtest `--exit-profile barrier`; record; commit.

### Task 3: Cost filter
- [ ] Tests: generator rejects when cost share > 20%.
- [ ] Implement; backtest; record; commit.

### Task 4: Outcome gate
- [ ] Tests: `signal_outcomes` per-signal R; `EdgeTable.fit` purge and min n; `apply` gate.
- [ ] Implement `src/edge_gate.py`; walk-forward gated backtest; save table; commit.

### Task 5: Live wiring
- [ ] Tests: AutoTrader `breakeven_mode`, time stop, `risk_pct` sizing, `edge_gate` candidates and opposing exit.
- [ ] Implement settings; suite green; commit.

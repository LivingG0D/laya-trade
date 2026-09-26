import numpy as np
import pandas as pd
import pytest

from experiments.eval_15m import (
    breakout_sides,
    ev_sides,
    pullback_sides,
    random_sides,
    signals_from_sides,
)


def test_pullback_needs_trend_and_rsi_recross():
    feats = pd.DataFrame({
        "rsi":            [35, 42, 35, 42, 65, 58, 65, 58],
        "d1_ema_dist_21": [.1, .1, -.1, -.1, -.1, -.1, .1, .1],
        "h4_ema_dist_50": [.1, .1, .1, .1, -.1, -.1, -.1, -.1],
    })
    sides = pullback_sides(feats)
    # bar 1: uptrend + RSI 35 -> 42 = long. bar 3: daily trend down -> no long.
    # bar 5: downtrend + RSI 65 -> 58 = short. bar 7: mixed trend -> nothing.
    assert list(sides) == [0, 1, 0, 0, 0, -1, 0, 0]


def test_breakout_uses_only_previous_bars():
    high = np.array([10, 11, 10.5, 10.8, 12.0, 11.0])
    low = np.array([9, 9.5, 9.2, 9.4, 11.0, 8.0])
    close = np.array([9.5, 10.5, 10, 10.5, 11.5, 8.5])
    sides = breakout_sides(high, low, close, lookback=3)
    # bar 4 closes 11.5 above the prior 3 highs (max 11) -> long; bar 5 closes 8.5 below prior lows (min 9.2) -> short
    assert list(sides) == [0, 0, 0, 0, 1, -1]


def test_ev_rule_picks_better_side_after_costs():
    p_long = np.array([0.40, 0.40, 0.20, 0.30])
    p_short = np.array([0.10, 0.10, 0.45, 0.30])
    cost_r = np.array([0.10, 0.20, 0.10, 0.10])
    # EV = 3p - 1 - cost: 0.10, 0.00, 0.25, -0.2 -> margin 0.1
    assert list(ev_sides(p_long, p_short, cost_r, margin=0.1)) == [1, 0, -1, 0]


def test_signals_from_sides_use_atr_bracket():
    closes = np.array([100.0, 200.0, 50.0])
    atr = np.array([1.0, 4.0, 0.5])
    signals = signals_from_sides(closes, atr, np.array([1, -1, 0]), stop_mult=1.5, target_mult=3.0,
                                 hold_seconds=14400)
    assert signals[0]["action"] == "LONG"
    assert signals[0]["stop_loss"] == pytest.approx(98.5) and signals[0]["take_profit_1"] == pytest.approx(103.0)
    assert signals[1]["stop_loss"] == pytest.approx(206.0) and signals[1]["take_profit_1"] == pytest.approx(188.0)
    assert signals[1]["max_hold_seconds"] == 14400
    assert signals[2] == {}


def test_random_sides_seeded_with_rate():
    first = random_sides(10000, rate=0.02, seed=1)
    assert np.array_equal(first, random_sides(10000, rate=0.02, seed=1))
    assert 150 < np.count_nonzero(first) < 250
    assert set(np.unique(first)) == {-1, 0, 1}

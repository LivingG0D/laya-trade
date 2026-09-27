import numpy as np
import pytest

from experiments.eval_trend import donchian_signals, max_drawdown_r, simulate

BAR = 86400


def bars(opens, highs, lows, closes):
    return {"time": np.arange(len(opens)) * BAR, "open": np.array(opens, float), "high": np.array(highs, float),
            "low": np.array(lows, float), "close": np.array(closes, float)}


def test_donchian_uses_only_previous_bars():
    high = np.array([10, 11, 10.5, 10.8, 12.0, 11.0, 9.0])
    low = np.array([9, 9.5, 9.2, 9.4, 11.0, 8.0, 8.5])
    close = np.array([9.5, 10.5, 10, 10.5, 11.5, 8.5, 8.9])
    entry, exit_long, exit_short = donchian_signals(high, low, close, entry=3, exit=2)
    # bar 4 closes 11.5 above the prior 3 highs (max 11) -> long; bar 5 closes 8.5 below prior 3 lows -> short
    assert list(entry) == [0, 0, 0, 0, 1, -1, 0]
    # bar 5 close 8.5 < min(low[3:5]) = 9.4 -> exit long; bar 4 close 11.5 > max(high[2:4]) = 10.8 -> exit short
    assert list(exit_long) == [False, False, False, False, False, True, False]
    assert list(exit_short) == [False, False, False, False, True, False, False]


def test_entry_at_next_open_and_gap_through_stop_fills_at_open():
    b = bars([100, 100, 101, 90], [101, 102, 103, 91], [99, 99, 100, 89], [100, 101, 102, 90])
    entry = np.array([1, 0, 0, 0])
    no_exit = np.zeros(4, bool)
    atr = np.full(4, 2.0)
    trades = simulate(b, entry, no_exit, no_exit, atr, fee=0.0, slip=0.0)
    (t,) = trades
    # enters at bar 1 open 100, stop 100 - 2*2 = 96; bar 3 opens at 90, below the stop -> fills at 90
    assert t["entry_time"] == BAR and t["exit_reason"] == "STOP" and t["exit_price"] == 90
    assert t["r_multiple"] == pytest.approx((90 - 100) / 4)
    assert t["initial_risk_pct"] == pytest.approx(0.04)


def test_channel_exit_at_next_open_then_reverse():
    b = bars([100, 100, 104, 103, 99], [101, 105, 105, 104, 100], [99, 99, 102, 98, 97], [100, 104, 103, 99, 98])
    entry = np.array([1, 0, 0, -1, 0])
    exit_long = np.array([False, False, False, True, False])
    no_exit = np.zeros(5, bool)
    trades = simulate(b, entry, exit_long, no_exit, np.full(5, 10.0), fee=0.0, slip=0.0)
    long, short = trades
    assert long["exit_reason"] == "CHANNEL" and long["exit_price"] == 99 and long["exit_time"] == 4 * BAR
    assert long["r_multiple"] == pytest.approx((99 - 100) / 20)
    assert short["side"] == "SHORT" and short["entry_price"] == 99 and short["exit_reason"] == "END"
    assert short["r_multiple"] == pytest.approx((99 - 98) / 20)


def test_long_pays_positive_funding_and_fees_count_in_r():
    b = bars([100, 100, 100], [101, 101, 101], [99, 99, 99], [100, 100, 100])
    entry = np.array([1, 0, 0])
    no_exit = np.zeros(3, bool)
    funding = (np.array([BAR + 3600, 2 * BAR + 3600]), np.array([0.001, 0.001]))
    (t,) = simulate(b, entry, no_exit, no_exit, np.full(3, 5.0), fee=0.0004, slip=0.0, funding=funding)
    # risk 10 per unit; fees 0.0004 * (100 + 100) = 0.08; funding 2 * 0.001 * 100 = 0.2
    assert t["cost_r"] == pytest.approx(0.028)
    assert t["r_multiple"] == pytest.approx(-0.028)


def test_entries_ignored_while_in_position():
    b = bars([100] * 5, [101] * 5, [99] * 5, [100] * 5)
    entry = np.array([1, 1, -1, 1, 0])
    no_exit = np.zeros(5, bool)
    trades = simulate(b, entry, no_exit, no_exit, np.full(5, 5.0), fee=0.0, slip=0.0)
    assert len(trades) == 1 and trades[0]["side"] == "LONG" and trades[0]["exit_reason"] == "END"


def test_max_drawdown_follows_exit_order():
    trades = [{"exit_time": 3, "r_multiple": -1.0}, {"exit_time": 1, "r_multiple": 2.0},
              {"exit_time": 2, "r_multiple": -1.5}]
    # cumulative in exit order: 2, 0.5, -0.5 -> peak 2, trough -0.5
    assert max_drawdown_r(trades) == pytest.approx(2.5)

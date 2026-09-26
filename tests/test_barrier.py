import numpy as np

from src.barrier import barrier_labels


def bars(rows):
    """rows of (open, high, low) -> arrays."""
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2]


def labels(rows, horizon=3):
    o, h, l = bars(rows)
    return barrier_labels(o, h, l, tp=0.012, sl=0.006, horizon=horizon)


def test_long_wins_when_target_comes_first():
    # bar 0 signals, entry at bar 1 open = 100; bar 2 reaches 101.2 before any 99.4
    long_win, short_win = labels([(100, 100, 100), (100, 100.5, 99.8), (100.5, 101.3, 100.2), (101, 101, 101)])
    assert long_win[0] == 1.0
    assert short_win[0] == 0.0


def test_stop_first_is_a_loss():
    long_win, _ = labels([(100, 100, 100), (100, 100.2, 99.3), (99.5, 101.5, 99.5), (101, 101, 101)])
    assert long_win[0] == 0.0


def test_same_bar_touching_both_counts_as_loss():
    long_win, short_win = labels([(100, 100, 100), (100, 101.5, 98.5), (100, 100, 100), (100, 100, 100)])
    assert long_win[0] == 0.0
    assert short_win[0] == 0.0


def test_timeout_is_a_loss_and_short_mirrors():
    flat = [(100, 100.1, 99.9)] * 4
    long_win, short_win = labels(flat)
    assert long_win[0] == 0.0 and short_win[0] == 0.0

    down = [(100, 100, 100), (100, 100.2, 99.9), (99.5, 99.6, 98.7), (99, 99, 99)]
    long_win, short_win = labels(down)
    assert short_win[0] == 1.0 and long_win[0] == 0.0


def test_bars_without_a_full_horizon_are_nan():
    rows = [(100, 100.1, 99.9)] * 6
    long_win, short_win = labels(rows, horizon=3)
    assert not np.isnan(long_win[2])  # needs bars 3..5
    assert np.isnan(long_win[3:]).all() and np.isnan(short_win[3:]).all()


def test_per_bar_targets_scale_each_signal():
    o, h, l = bars([(100, 100, 100), (100, 100.5, 99.8), (100.5, 101.3, 100.2), (101, 101, 101),
                    (101, 101, 101)])
    # Bar 0 needs +0.4% (hit), bar 1 needs +5% (never hit).
    tp = np.array([0.004, 0.05, 0.05, 0.05, 0.05])
    sl = np.array([0.006, 0.006, 0.006, 0.006, 0.006])
    long_win, _ = barrier_labels(o, h, l, tp=tp, sl=sl, horizon=3)
    assert long_win[0] == 1.0 and long_win[1] == 0.0


def test_undefined_per_bar_target_leaves_label_nan():
    o, h, l = bars([(100, 100, 100), (100, 101.5, 99.9), (100, 100, 100), (100, 100, 100)])
    tp = np.array([np.nan, 0.012, 0.012, 0.012])
    long_win, short_win = barrier_labels(o, h, l, tp=tp, sl=0.006, horizon=3)
    assert np.isnan(long_win[0]) and np.isnan(short_win[0])

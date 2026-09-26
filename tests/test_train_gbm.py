import numpy as np
import pytest

from experiments.train_gbm import (
    DAY,
    barrier_signals,
    random_barrier_signals,
    train_mask,
    walk_forward_schedule,
)


def test_schedule_trains_first_then_steps_and_holds_out_the_end():
    start, end = 0, 365 * DAY
    windows, holdout = walk_forward_schedule(start, end, first_train_days=120, step_days=30, holdout_days=60)

    assert windows[0] == (120 * DAY, 150 * DAY)
    assert all(b[0] == a[1] for a, b in zip(windows, windows[1:]))  # contiguous
    assert windows[-1][1] == 305 * DAY  # stops where the holdout begins
    assert holdout == (305 * DAY, 365 * DAY)
    # 120 + 6 * 30 = 300: the 5-day stub before the holdout folds into the last window
    assert len(windows) == 6 and windows[-1] == (270 * DAY, 305 * DAY)


def test_train_mask_purges_labels_that_overlap_the_test_window():
    times = np.arange(0, 100) * 300
    test_start = 60 * 300
    mask = train_mask(times, test_start, label_seconds=25 * 300)
    # A bar's label needs the next 25 bars: bar 35 resolves exactly at bar 60's open.
    assert mask[:36].all() and not mask[36:].any()


def test_barrier_signals_take_the_better_side_above_threshold():
    closes = np.array([100.0, 200.0, 50.0, 10.0])
    p_long = np.array([0.50, 0.30, 0.46, 0.2])
    p_short = np.array([0.20, 0.47, 0.48, 0.1])
    signals = barrier_signals(closes, p_long, p_short, threshold=0.45)

    assert signals[0]["action"] == "LONG"
    assert signals[0]["stop_loss"] == pytest.approx(99.4)
    assert signals[0]["take_profit_1"] == pytest.approx(101.2)
    assert signals[0]["max_hold_seconds"] == 7200
    assert signals[1]["action"] == "SHORT" and signals[1]["stop_loss"] == pytest.approx(201.2)
    assert signals[2]["action"] == "SHORT"  # 0.48 beats 0.46
    assert signals[3] == {}


def test_random_barrier_signals_are_seeded():
    closes = np.full(5000, 100.0)
    first = random_barrier_signals(closes, rate=0.05, seed=3)
    assert first == random_barrier_signals(closes, rate=0.05, seed=3)
    assert first != random_barrier_signals(closes, rate=0.05, seed=4)
    assert 150 < sum(1 for s in first if s) < 350


def test_label_metrics_report_top_bucket_hit_rates_and_direction_skill():
    import pandas as pd

    from experiments.train_gbm import label_metrics

    n = 1000
    rng = np.random.default_rng(0)
    p_long = rng.random(n)
    y_long = (p_long > 0.99).astype(float)  # only the top 1% win
    df = pd.DataFrame({"p_long": p_long, "y_long": y_long, "p_short": 1 - p_long,
                       "y_short": (p_long < 0.01).astype(float)})
    m = label_metrics(df, threshold=0.45)
    assert m["long"]["top_1pct_hit_rate"] == pytest.approx(1.0)
    # Among bars where exactly one side won, p_long - p_short separates them perfectly.
    assert m["direction_auc"] == pytest.approx(1.0)


def test_subsample_keeps_every_symbol():
    from experiments.train_gbm import subsample_mask

    times = np.repeat(np.arange(10) * 300, 10)  # 10 symbols at every timestamp, sorted by time
    symbols = np.tile(np.arange(10), 10)
    keep = subsample_mask(times, 2)
    assert set(symbols[keep]) == set(range(10))
    assert keep.sum() == 50


def test_pass_checks_count_every_window_and_need_enough_holdout_trades():
    from experiments.train_gbm import pass_checks

    good = {"trade_count": 40, "expectancy_r": 0.3, "avg_cost_r": 0.13}
    random_block = {"trade_count": 40, "expectancy_r": 0.0}
    windows = [{"model": good}] * 2 + [{"model": {"trade_count": 0, "expectancy_r": 0.0}}] * 3
    checks = pass_checks(windows, {"model": good, "random": random_block},
                         {"model": good, "random": random_block}, round_trip_cost_r=0.2)
    assert not checks["windows_positive_60pct"]  # 2 of 5 windows, not 2 of 2
    assert checks["beats_random_by_costs"]  # 0.3 - 0.0 > 0.2

    few = {"trade_count": 5, "expectancy_r": 0.5, "avg_cost_r": 0.13}
    checks = pass_checks([{"model": good}] * 5, {"model": good, "random": random_block},
                         {"model": few, "random": random_block}, round_trip_cost_r=0.2)
    assert not checks["holdout_expectancy_positive"]

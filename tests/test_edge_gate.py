import pytest

from src.edge_gate import EdgeTable, bucket_key, gate_walk_forward, signal_outcomes
from src.strategy_evaluator import CostModel, EvaluationConfig

FREE = CostModel(fee_bps=0, slippage_bps=0)


def candle(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def long_signal(conf=72.0):
    return {"action": "LONG", "confidence_pct": conf, "stop_loss": 99.0, "take_profit_1": 102.0, "take_profit_2": 0}


def test_bucket_key_groups_by_side_and_five_point_confidence():
    assert bucket_key(long_signal(72.4)) == "LONG:70"
    assert bucket_key({"action": "SHORT", "confidence_pct": 55.0}) == "SHORT:55"
    assert bucket_key({"action": "NEUTRAL", "confidence_pct": 90.0}) is None


def test_signal_outcomes_simulate_each_signal_alone():
    candles = [
        candle(0, 100, 100.5, 99.5, 100),
        candle(300, 100, 102.5, 99.5, 102),  # TP hit: +2R
        candle(600, 102, 102.5, 98.5, 99),
        candle(900, 99, 99.5, 97.0, 98),
    ]
    signals = [long_signal(), long_signal(), {}, {}]

    outcomes = signal_outcomes(candles, signals, EvaluationConfig(min_confidence_pct=0), FREE, horizon_bars=5)

    assert outcomes[0] == pytest.approx(2.0)
    # Second signal enters at 102 (stop 99, target 102 invalid for a long) -> no trade
    assert outcomes[1] is None
    assert outcomes[2] is None and outcomes[3] is None


def test_table_needs_min_samples_and_positive_mean():
    signals = [long_signal(71)] * 40 + [{"action": "SHORT", "confidence_pct": 61.0}] * 40
    outcomes = [0.5] * 40 + [-0.2] * 40
    table = EdgeTable.fit(signals, outcomes, min_samples=30)

    assert table.expected_r(long_signal(73)) == pytest.approx(0.5)
    assert table.allows(long_signal(73))
    assert not table.allows({"action": "SHORT", "confidence_pct": 62.0})
    assert table.expected_r(long_signal(81)) is None  # unseen bucket
    assert not EdgeTable.fit(signals[:10], outcomes[:10], min_samples=30).allows(long_signal(71))


def test_table_round_trips_through_json(tmp_path):
    table = EdgeTable.fit([long_signal()] * 30, [0.3] * 30, min_samples=30)
    path = tmp_path / "edge.json"
    table.save(path)
    assert EdgeTable.load(path).expected_r(long_signal()) == pytest.approx(0.3)


def test_gate_walk_forward_fits_only_on_outcomes_resolved_before_each_window():
    n = 100
    signals = [long_signal()] * n
    # Outcomes flip sign halfway; with no look-ahead the first test window sees only positives.
    outcomes = [1.0] * 50 + [-1.0] * 50
    gated = gate_walk_forward(signals, outcomes, window_bars=40, horizon_bars=5, min_samples=10)

    assert all(g == {} for g in gated[:40])  # training-only window never trades
    # Window 40-80 is fit on signals 0..34 (resolved before bar 40) -> all positive -> kept.
    assert all(g for g in gated[40:80])
    # Window 80-100 is fit on signals 0..74: mean = (50 - 25) / 75 > 0 -> still kept.
    assert all(g for g in gated[80:])
    tighter = gate_walk_forward(signals, [1.0] * 30 + [-1.0] * 70, window_bars=40, horizon_bars=5, min_samples=10)
    assert all(g == {} for g in tighter[80:])  # fit on 0..74: 30 up, 45 down -> negative

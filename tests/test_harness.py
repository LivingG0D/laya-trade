import pytest

from experiments.harness import BARRIER_BREAK_EVEN, matched_rate, round_trip_cost_r
from experiments.train_gbm import random_barrier_signals
from src.config import ROUND_TRIP_COST_PCT
from src.strategy_evaluator import CostModel
from src.trade_plan import TradePlan


def test_one_cost_source_for_live_and_research():
    assert CostModel().round_trip == pytest.approx(ROUND_TRIP_COST_PCT)
    maker = CostModel(entry_fee_bps=2.0, entry_slippage_bps=0.0)
    assert maker.round_trip == pytest.approx((2 + 0 + 4 + 2) / 1e4)
    assert BARRIER_BREAK_EVEN == pytest.approx((0.006 + ROUND_TRIP_COST_PCT) / 0.018)


def test_round_trip_cost_in_r_scales_with_each_trades_risk():
    trades = [{"initial_risk_pct": 0.006}, {"initial_risk_pct": 0.012}]
    assert round_trip_cost_r(trades, CostModel()) == pytest.approx((0.2 + 0.1) / 2)
    assert round_trip_cost_r([], CostModel()) == 0.0


def test_matched_rate_rescales_until_the_probe_hits_the_target():
    # Half of all entries are skipped (open trades), so the rate must double.
    rate = matched_rate(100, 0.01, probe=lambda r: int(r * 10_000 / 2))
    assert rate == pytest.approx(0.02)
    assert matched_rate(100, 0.01, probe=lambda r: 0) == 0.01  # no probe trades: rate unchanged


def test_random_barrier_signals_carry_a_valid_plan():
    signals = random_barrier_signals([100.0] * 2000, rate=0.05, seed=(1, 0, 0))
    plans = [TradePlan.from_signal(s) for s in signals if s]
    assert 50 < len(plans) < 150 and {p.side for p in plans} == {1, -1}
    long = next(p for p in plans if p.side == 1)
    assert (long.stop, long.tp1, long.max_hold_seconds) == pytest.approx((99.4, 101.2, 7200))

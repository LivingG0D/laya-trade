import asyncio
import time

import pytest

from src.config import ROUND_TRIP_COST_PCT
from src.signal_generator import SignalGenerator
from src.strategy_evaluator import CostModel, EvaluationConfig, evaluate_signal_sequence
from src.trade_management import (
    ExitPolicy,
    breakeven_stop,
    breakeven_trigger_price,
    fallback_bracket,
    opposing_exit,
    time_stop_due,
    trigger_exit,
)
from src.trade_plan import TradePlan
from tests.test_auto_trader_multi import make_trader

LEGACY = ExitPolicy(breakeven="legacy", leverage=50)
FREE = CostModel(fee_bps=0, slippage_bps=0)


def test_legacy_breakeven_fires_at_nine_percent_return_on_margin_or_tp1():
    trigger = 100 * (1 + 0.09 / 50)
    assert breakeven_stop(LEGACY, 1, 100.0, 102.0, trigger - 0.001) is None
    assert breakeven_stop(LEGACY, 1, 100.0, 102.0, trigger) == pytest.approx(100.05)
    assert breakeven_stop(LEGACY, -1, 100.0, 98.0, 100 * (1 - 0.09 / 50)) == pytest.approx(99.95)
    assert breakeven_trigger_price(LEGACY, 1, 100.0, 102.0) == pytest.approx(trigger)


def test_tp1_fees_breakeven_ignores_return_on_margin_and_covers_costs():
    policy = ExitPolicy(breakeven="tp1_fees")
    assert breakeven_stop(policy, 1, 100.0, 102.0, 101.9) is None
    assert breakeven_stop(policy, 1, 100.0, 102.0, 102.0) == pytest.approx(100 * (1 + ROUND_TRIP_COST_PCT))
    assert breakeven_trigger_price(policy, 1, 100.0, 102.0) == 102.0
    assert breakeven_stop(ExitPolicy(breakeven="off"), 1, 100.0, 102.0, 200.0) is None
    assert breakeven_trigger_price(ExitPolicy(breakeven="off"), 1, 100.0, 102.0) is None


def test_bracket_fill_prefers_the_stop_and_fills_gaps_at_the_open():
    assert trigger_exit(1, 99.0, 102.0, 104.0, False, open_=100, high=103, low=98.5) == ("STOP", 99.0)
    assert trigger_exit(1, 99.0, 102.0, 104.0, False, open_=98.0, high=98.5, low=97.0) == ("STOP", 98.0)
    assert trigger_exit(1, 99.0, 102.0, 104.0, False, open_=100, high=105, low=99.5) == ("TP1", 102.0)
    assert trigger_exit(1, 99.0, 102.0, 104.0, True, open_=100, high=105, low=99.5) == ("TP2", 104.0)
    assert trigger_exit(-1, 101.0, 98.0, 0.0, False, open_=102, high=102.5, low=101.5) == ("STOP", 102.0)
    assert trigger_exit(1, 0.0, 0.0, 0.0, False, open_=100, high=200, low=1) is None  # no bracket


def test_opposing_exit_needs_an_opposite_confident_plan_or_the_gate():
    policy = ExitPolicy(opposing_min_confidence=70.0)
    short = {"action": "SHORT", "confidence_pct": 75.0}
    assert opposing_exit(policy, 1, short)
    assert not opposing_exit(policy, -1, short)
    assert not opposing_exit(policy, 1, dict(short, confidence_pct=65.0))
    assert not opposing_exit(policy, 1, {"action": "NEUTRAL", "confidence_pct": 99.0})
    assert opposing_exit(policy, 1, dict(short, confidence_pct=10.0), gate=lambda signal: True)
    assert not opposing_exit(ExitPolicy(), 1, short)  # off


def test_time_stop_and_fallback_bracket():
    assert time_stop_due(0.0, 7200, 7200.0) and not time_stop_due(0.0, 7200, 7199.0)
    assert not time_stop_due(0.0, None, 1e9)
    stop, tp1, tp2 = fallback_bracket(-1, 100.0, 50)
    assert (stop, tp1, tp2) == pytest.approx((100.5, 99.2, 98.8))


def test_policy_from_settings_matches_the_auto_trader_switches():
    assert ExitPolicy.from_settings({}) == ExitPolicy(breakeven="legacy", leverage=50, opposing_min_confidence=70.0)
    off = ExitPolicy.from_settings({"auto_breakeven": False, "opposing_reversal_exit": False, "leverage": 20})
    assert off == ExitPolicy(breakeven="off", leverage=20, opposing_min_confidence=None)


def test_live_and_backtest_move_the_stop_at_the_same_price(tmp_path, monkeypatch):
    """Parity by construction: one rule, mark price live, bar high in the backtest."""
    favourable = 100.2  # past the legacy trigger (100.18 at 50x), below TP1
    candles = [
        {"time": 0, "open": 100, "high": 100.1, "low": 99.9, "close": 100, "volume": 1},
        {"time": 300, "open": 100, "high": favourable, "low": 99.95, "close": 100.1, "volume": 1},
        {"time": 600, "open": 100.1, "high": 100.1, "low": 99.0, "close": 99.2, "volume": 1},
    ]
    plan = TradePlan(side=1, entry=100.0, stop=99.4, tp1=102.0, confidence_pct=80.0)
    result = evaluate_signal_sequence(candles, [plan.to_signal(), {}, {}], EvaluationConfig(exits=LEGACY), FREE)
    backtest_stop = result["trades"][0]["exit_price"]

    trader, client = make_trader(tmp_path, monkeypatch)
    trader.settings.update(breakeven_mode="legacy", leverage=50, opposing_reversal_exit=False)
    trader._set_active_trade({
        "symbol": "BTCUSDT.P", "exchange_symbol": "BTC-SWAP-USDT", "side": "LONG", "entry_price": 100.0,
        "stop_loss": 99.4, "take_profit_1": 102.0, "take_profit_2": 0.0, "be_moved": False, "open_time": time.time(),
    })
    asyncio.run(trader._manage_position({
        "rawSymbol": "BTC-SWAP-USDT", "displaySymbol": "BTCUSDT.P", "side": "LONG", "entryPrice": 100.0,
        "markPrice": favourable, "quantity": 1.0,
    }))
    live_stop = client.set_position_tpsl.await_args.kwargs["stop_loss"]

    assert result["trades"][0]["exit_reason"] == "BREAKEVEN"
    assert live_stop == pytest.approx(backtest_stop) == pytest.approx(100.05)


def test_unprotected_position_gets_a_same_side_bracket_only(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)  # FakeSignalGenerator always plans a LONG
    trader.settings.update(opposing_reversal_exit=False, leverage=50)
    short = {"rawSymbol": "BTC-SWAP-USDT", "displaySymbol": "BTCUSDT.P", "side": "SHORT", "entryPrice": 100.0,
             "markPrice": 100.0, "quantity": 1.0}

    asyncio.run(trader._manage_position(short))

    kwargs = client.set_position_tpsl.await_args_list[0].kwargs
    assert (kwargs["stop_loss"], kwargs["take_profit"]) == pytest.approx((100.5, 99.2))  # fallback, not the long plan


def test_signal_generator_output_parses_to_its_plan():
    state = {"price": 100.0, "symbol": "BTCUSDT.P", "interval": "5m", "atr": 0.3, "atr_pct": 0.3,
             "rsi": 55.0, "adx": 30.0, "trend_state": "BULLISH", "htf": {"trend": "BULLISH"}}
    generator = SignalGenerator(exit_profile="barrier")
    signal = generator.generate_signal(state, {"position": "STRONG_LONG", "directional_conviction": 0.8})

    plan = TradePlan.from_signal(signal)
    assert plan == TradePlan(side=1, entry=100.0, stop=99.4, tp1=101.2, tp2=0.0, confidence_pct=80.0,
                             max_hold_seconds=7200)
    assert "targets" not in signal
    # The displayed break-even trigger is the legacy rule's: 9% ROE at 50x, before TP1.
    assert signal["break_even"] == pytest.approx(100.18)
    neutral = generator.generate_signal(state, {"position": "NEUTRAL_HOLD"})
    assert TradePlan.from_signal(neutral) is None

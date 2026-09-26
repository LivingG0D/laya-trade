import pytest

from src.indicators import compute_all_indicators
from src.signal_generator import SignalGenerator
from src.strategy_evaluator import (
    CostModel,
    EvaluationConfig,
    evaluate_signal_sequence,
    walk_forward_windows,
)


def candle(time, open_price, high, low, close):
    return {
        "time": time,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
    }


def signal(action="LONG", stop=95.0, tp1=110.0, tp2=120.0, confidence=80.0):
    return {
        "action": action,
        "confidence_pct": confidence,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
    }


def test_evaluator_enters_on_next_candle_open():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 110, 112, 109, 111),
        candle(3, 111, 113, 110, 112),
    ]
    signals = [signal(stop=105, tp1=125, tp2=0), {}, {}]

    result = evaluate_signal_sequence(
        candles,
        signals,
        EvaluationConfig(risk_per_trade=0.01, max_leverage=10),
        CostModel(fee_bps=0, slippage_bps=0, funding_bps_per_bar=0),
    )

    assert result["trade_count"] == 1
    assert result["trades"][0]["entry_price"] == 110.0


def test_evaluator_assumes_stop_before_target_on_same_candle():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 110, 90, 100),
        candle(3, 100, 101, 99, 100),
    ]
    signals = [signal(stop=95, tp1=105), {}, {}]

    result = evaluate_signal_sequence(
        candles,
        signals,
        EvaluationConfig(risk_per_trade=0.01, max_leverage=10),
        CostModel(fee_bps=0, slippage_bps=0, funding_bps_per_bar=0),
    )

    assert result["trades"][0]["exit_reason"] == "STOP"
    assert result["net_profit"] < 0


def test_evaluator_costs_reduce_net_result():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 106, 99, 105),
        candle(3, 105, 107, 104, 106),
    ]
    signals = [signal(stop=95, tp1=106, tp2=110), {}, {}]

    free = evaluate_signal_sequence(
        candles,
        signals,
        EvaluationConfig(),
        CostModel(fee_bps=0, slippage_bps=0, funding_bps_per_bar=0),
    )
    costed = evaluate_signal_sequence(
        candles,
        signals,
        EvaluationConfig(),
        CostModel(fee_bps=8, slippage_bps=5, funding_bps_per_bar=1),
    )

    assert costed["net_profit"] < free["net_profit"]


def test_indicator_rejects_malformed_or_unordered_candles():
    with pytest.raises(ValueError, match="strictly ordered"):
        compute_all_indicators([
            candle(1, 100, 101, 99, 100),
            candle(1, 100, 101, 99, 100),
        ])

    with pytest.raises(ValueError, match="inconsistent high/low"):
        compute_all_indicators([candle(1, 100, 98, 99, 100)])


def test_signal_uses_configured_market_leverage():
    signal = SignalGenerator(leverage=50).generate_signal(
        {
            "price": 100.0,
            "symbol": "BTCUSDT.P",
            "interval": "5m",
            "leverage": 10,
            "atr": 2.0,
            "atr_pct": 2.0,
            "support": 98.0,
            "resistance": 102.0,
            "rsi": 55.0,
            "adx": 30.0,
            "trend_state": "BULLISH",
            "htf": {"trend": "BULLISH"},
        },
        {
            "position": "STRONG_LONG",
            "long_prob": 0.7,
            "short_prob": 0.2,
            "hold_prob": 0.1,
            "directional_conviction": 0.77,
            "confidence_score": 3,
        },
    )

    assert signal["leverage"] == 10.0
    assert "10x" in signal["reasons"][0]
    assert "50x" not in signal["reasons"][0]


def test_walk_forward_windows_are_chronological_and_expanding():
    windows = list(walk_forward_windows(10, train_size=4, test_size=3, step=3))

    assert windows == [
        {"train_start": 0, "train_end": 4, "test_start": 4, "test_end": 7},
        {"train_start": 0, "train_end": 7, "test_start": 7, "test_end": 10},
    ]


def test_evaluator_rejects_mismatched_signal_length():
    with pytest.raises(ValueError, match="same length"):
        evaluate_signal_sequence(
            [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)],
            [signal()],
        )


FREE = CostModel(fee_bps=0, slippage_bps=0, funding_bps_per_bar=0)


def test_breakeven_trigger_moves_stop_to_entry_for_later_bars():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 103, 99, 102),  # +3% high crosses the 2% trigger
        candle(3, 102, 102, 99.9, 100),  # dips below entry, far above the 95 stop
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(breakeven_trigger_pct=0.02), FREE
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "BREAKEVEN"
    assert trade["exit_price"] == pytest.approx(100 * 1.0005)


def test_breakeven_off_by_default():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 103, 99, 102),
        candle(3, 102, 102, 99.9, 100),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(candles, signals, EvaluationConfig(), FREE)

    assert result["trades"][0]["exit_reason"] == "END_OF_DATA"


def test_tp1_fill_moves_stop_to_breakeven():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 111, 99, 108),  # TP1 fills, trigger far away
        candle(3, 108, 108, 99.9, 100),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(breakeven_trigger_pct=0.5), FREE
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "BREAKEVEN"
    assert trade["exit_price"] == pytest.approx(100 * 1.0005)
    assert trade["net_pnl"] > 0  # half booked at TP1


def test_opposing_signal_exits_at_next_open():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 102, 99, 101),
        candle(3, 101, 102, 100, 101),
        candle(4, 101, 102, 100, 101),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, signal("SHORT", 106, 90, 85, 80), {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(opposing_exit_confidence=70), FREE
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "OPPOSING"
    assert trade["exit_price"] == 101
    assert result["trade_count"] == 1


def test_weak_opposing_signal_does_not_exit():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 102, 99, 101),
        candle(3, 101, 102, 100, 101),
        candle(4, 101, 102, 100, 101),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, signal("SHORT", 106, 90, 85, 60), {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(opposing_exit_confidence=70), FREE
    )

    assert result["trades"][0]["exit_reason"] == "END_OF_DATA"


def test_trades_report_cost_as_share_of_risk():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 111, 99, 108),
        candle(3, 108, 121, 107, 120),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(), CostModel(fee_bps=10, slippage_bps=0)
    )

    trade = result["trades"][0]
    # risk 5 per unit; fees 0.1% on entry 100 and exits 110/120 -> 0.1 + 0.055 + 0.06 per unit
    assert trade["cost_r"] == pytest.approx((0.1 + 0.055 + 0.06) / 5)
    assert result["avg_cost_r"] == pytest.approx(trade["cost_r"])


def test_stop_gapped_through_fills_at_open_not_stop():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 100.3, 99.8, 99.9),  # wick arms break-even at 100.05
        candle(3, 99.7, 99.9, 99.5, 99.6),  # opens below the new stop
    ]
    signals = [signal(stop=98, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(breakeven_trigger_pct=0.0018), FREE
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "BREAKEVEN"
    assert trade["exit_price"] == 99.7
    assert trade["net_pnl"] < 0


def test_short_stop_gapped_through_fills_at_open():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 101, 99, 100),
        candle(3, 106, 107, 105, 106),  # opens above the 105 stop
    ]
    signals = [signal("SHORT", 105, 90, 85), {}, {}]

    result = evaluate_signal_sequence(candles, signals, EvaluationConfig(), FREE)

    assert result["trades"][0]["exit_price"] == 106


def test_breakeven_after_tp1_ignores_price_wicks():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 103, 99, 102),  # wick, no TP1
        candle(3, 102, 102, 99.9, 100),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(breakeven_after_tp1=True, breakeven_offset_pct=0.0012), FREE
    )

    assert result["trades"][0]["exit_reason"] == "END_OF_DATA"


def test_breakeven_after_tp1_covers_costs():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 111, 99, 108),  # TP1 fills
        candle(3, 108, 108, 99.9, 100),
    ]
    signals = [signal(stop=95, tp1=110, tp2=120), {}, {}]

    result = evaluate_signal_sequence(
        candles, signals, EvaluationConfig(breakeven_after_tp1=True, breakeven_offset_pct=0.0012), FREE
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "BREAKEVEN"
    assert trade["exit_price"] == pytest.approx(100.12)


def test_time_stop_closes_at_bar_close_after_max_hold():
    candles = [
        candle(0, 100, 101, 99, 100),
        candle(300, 100, 101, 99, 100.5),
        candle(600, 100.5, 101, 99, 100.8),  # 600 s held at this close
        candle(900, 100.8, 101, 99, 100),
    ]
    held = dict(signal(stop=95, tp1=110, tp2=0), max_hold_seconds=600)
    signals = [held, {}, {}, {}]

    result = evaluate_signal_sequence(candles, signals, EvaluationConfig(), FREE)

    trade = result["trades"][0]
    assert trade["exit_reason"] == "TIME"
    assert trade["exit_price"] == 100.8


def test_barrier_profile_uses_model_label_bracket():
    state = {
        "price": 100.0, "symbol": "BTCUSDT.P", "interval": "5m", "atr": 0.3, "atr_pct": 0.3,
        "rsi": 55.0, "adx": 30.0, "trend_state": "BULLISH", "htf": {"trend": "BULLISH"},
    }
    decision = {"position": "STRONG_LONG", "directional_conviction": 0.8}

    sig = SignalGenerator(exit_profile="barrier").generate_signal(dict(state), decision)

    assert sig["stop_loss"] == pytest.approx(99.4)
    assert sig["take_profit_1"] == pytest.approx(101.2)
    assert sig["take_profit_2"] == 0
    assert sig["max_hold_seconds"] == 7200

    short = SignalGenerator(exit_profile="barrier").generate_signal(
        dict(state, trend_state="BEARISH", htf={"trend": "BEARISH"}),
        {"position": "STRONG_SHORT", "directional_conviction": 0.8},
    )
    assert short["stop_loss"] == pytest.approx(100.6)
    assert short["take_profit_1"] == pytest.approx(98.8)


def test_cost_filter_rejects_trades_where_costs_eat_the_risk():
    state = {
        "price": 100.0, "symbol": "BTCUSDT.P", "interval": "5m", "atr": 0.1, "atr_pct": 0.1, "support": 90.0,
        "rsi": 55.0, "adx": 30.0, "trend_state": "BULLISH", "htf": {"trend": "BULLISH"},
    }
    decision = {"position": "STRONG_LONG", "directional_conviction": 0.8}

    # Legacy stop clamps to 0.5% here -> 0.12% costs are 24% of risk.
    assert SignalGenerator(max_cost_share=0.20).generate_signal(dict(state), decision)["action"] == "NEUTRAL"
    assert SignalGenerator(max_cost_share=0.30).generate_signal(dict(state), decision)["action"] == "LONG"
    assert SignalGenerator().generate_signal(dict(state), decision)["action"] == "LONG"


def test_entry_side_costs_can_differ_from_exit():
    candles = [
        candle(1, 100, 101, 99, 100),
        candle(2, 100, 111, 99, 108),
        candle(3, 108, 121, 107, 120),
    ]
    signals = [signal(stop=95, tp1=110, tp2=0), {}, {}]
    maker = CostModel(fee_bps=10, slippage_bps=5, entry_fee_bps=0, entry_slippage_bps=0)

    result = evaluate_signal_sequence(candles, signals, EvaluationConfig(), maker)

    trade = result["trades"][0]
    assert trade["entry_price"] == 100  # no entry slippage
    assert trade["initial_risk_pct"] == pytest.approx(0.05)
    # only the exit pays: fee on the slipped exit fill, no entry fee
    exit_fill = 110 * (1 - 0.0005)
    assert trade["cost_r"] == pytest.approx(exit_fill * 0.001 * 2 / 10)

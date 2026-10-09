import asyncio
import time

from src.market_snapshot import closed_window, merge_timeframes, timeframe_snapshot
from tests.test_auto_trader_multi import FakeMarketService, make_trader


class RecordingEngine:
    def __init__(self):
        self.states = []

    async def decide(self, market_state):
        self.states.append(market_state)
        return {"position": "NEUTRAL_HOLD"}


def test_shared_builder_matches_live_analysis_state(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    engine = RecordingEngine()
    trader.laya_engine = engine

    asyncio.run(trader._analyze_symbol("BTCUSDT.P"))

    market = FakeMarketService()
    snapshots = {
        tf: timeframe_snapshot(closed_window(asyncio.run(market.fetch_klines("BTCUSDT.P", tf)), tf, time.time()),
                               "BTCUSDT.P", tf, 50)
        for tf in ("5m", "15m", "1h")
    }
    live_state = engine.states[0]
    rebuilt = merge_timeframes(snapshots)
    # FakeMarketService stamps candles with time.time(); compare everything else.
    for state in (live_state, rebuilt):
        state.pop("time")
        for row in state["multi_timeframe"].values():
            row.pop("time")
    assert rebuilt == live_state


class FakeAgent:
    def __init__(self):
        self.texts = []

    def predict(self, text, questions):
        self.texts.append(text)
        return {"answers": {"position": {"choice": "SCALP_LONG", "probabilities": {
            "STRONG_LONG": 0.30, "SCALP_LONG": 0.25, "NEUTRAL_HOLD": 0.25,
            "SCALP_SHORT": 0.12, "STRONG_SHORT": 0.08}}}}

    def predict_batch(self, texts, questions, batch_size=None):
        return [self.predict(text, questions) for text in texts]


LIVE_STATE = {
    "symbol": "BTCUSDT.P", "interval": "5m", "price": 100.0, "rsi": 58.0, "macd_hist": 0.2,
    "trend_state": "BULLISH", "bb_pct_b": 0.6, "vol_spike": 1.1, "adx": 27.0,
    "plus_di": 30.0, "minus_di": 18.0, "market_regime": "STRONG_TREND",
    "htf": {"trend": "BULLISH"},
}


def test_engine_split_reproduces_live_decision():
    from src.laya_engine import LayaEngine

    engine = LayaEngine(device="cpu")
    engine._agent = FakeAgent()
    live = asyncio.run(engine.decide(dict(LIVE_STATE)))

    model = engine._agent_decisions([dict(LIVE_STATE)])[0]
    blended = engine._blend(model, engine._fallback_decision(dict(LIVE_STATE), 0.0))

    for key in ("position", "long_prob", "short_prob", "hold_prob", "directional_conviction"):
        assert blended[key] == live[key]
    assert engine._agent.texts[0] == engine._agent.texts[1] == engine._agent_summary(LIVE_STATE)


def test_closed_window_excludes_candles_not_closed_at_bar_close():
    hour = [{"time": t} for t in range(0, 10 * 3600, 3600)]
    # 5m bar opening 04:55 closes 05:00 -> the 04:00 hour candle has just closed.
    window = closed_window(hour, "1h", at=5 * 3600, size=3)
    assert [c["time"] for c in window] == [2 * 3600, 3 * 3600, 4 * 3600]
    # One second earlier the 04:00 candle is still forming.
    assert closed_window(hour, "1h", at=5 * 3600 - 1, size=3)[-1]["time"] == 3 * 3600


def test_live_state_never_includes_the_forming_bar(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    engine = RecordingEngine()
    trader.laya_engine = engine
    asyncio.run(trader._analyze_symbol("BTCUSDT.P"))
    # FakeMarketService's newest 5m candle opened just now, so it is still forming.
    assert time.time() - engine.states[0]["time"] >= 300


def test_random_decisions_are_seeded_and_match_rate():
    from experiments.strategy_baseline import random_decisions

    first = random_decisions(2000, rate=0.1, seed=7)
    assert first == random_decisions(2000, rate=0.1, seed=7)
    assert first != random_decisions(2000, rate=0.1, seed=8)
    directional = [d for d in first if d["position"] != "NEUTRAL_HOLD"]
    assert 150 < len(directional) < 250
    assert {d["position"] for d in directional} == {"STRONG_LONG", "STRONG_SHORT"}


def test_ema_cross_decisions_fire_only_on_crosses():
    from experiments.strategy_baseline import ema_cross_decisions

    states = [
        {"ema9": 1.0, "ema21": 2.0},
        {"ema9": 3.0, "ema21": 2.0},  # cross up
        {"ema9": 4.0, "ema21": 2.0},  # still above
        {"ema9": 1.0, "ema21": 2.0},  # cross down
        {"ema9": None, "ema21": 2.0},
    ]
    positions = [d["position"] for d in ema_cross_decisions(states)]
    assert positions == ["NEUTRAL_HOLD", "STRONG_LONG", "NEUTRAL_HOLD", "STRONG_SHORT", "NEUTRAL_HOLD"]


def test_tradeable_rate_counts_only_signals_that_pass_the_confidence_gate():
    from experiments.strategy_baseline import tradeable_rate

    signals = [
        {"action": "LONG", "confidence_pct": 70.0},
        {"action": "SHORT", "confidence_pct": 60.0},  # below gate, never trades
        {"action": "NEUTRAL", "confidence_pct": 90.0},
        {"action": "LONG", "confidence_pct": 65.0},
    ]
    assert tradeable_rate(signals, 65.0) == 0.5
    assert tradeable_rate([], 65.0) == 0.0


def test_dashboard_and_auto_trader_decide_on_the_same_state(tmp_path, monkeypatch):
    from src.market_snapshot import dashboard_timeframes, market_state

    trader, _ = make_trader(tmp_path, monkeypatch)  # timeframes 5m, 15m, 1h
    engine = RecordingEngine()
    trader.laya_engine = engine
    asyncio.run(trader._analyze_symbol("BTCUSDT.P"))

    dashboard = asyncio.run(market_state(FakeMarketService().fetch_klines, "BTCUSDT.P", dashboard_timeframes("5m"),
                                         trader.settings["leverage"], entry_timeframe="5m"))
    assert dashboard_timeframes("5m") == ["5m", "15m", "1h"]
    for state in (dashboard, engine.states[0]):  # fake candles are stamped with the current second
        state.pop("time")
        for row in state["multi_timeframe"].values():
            row.pop("time")
    assert dashboard == engine.states[0]

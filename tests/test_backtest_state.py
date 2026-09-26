import asyncio

from src.auto_trader import merge_timeframes, timeframe_snapshot
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
        tf: timeframe_snapshot(asyncio.run(market.fetch_klines("BTCUSDT.P", tf)), "BTCUSDT.P", tf, 50)
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


def test_htf_window_excludes_candles_not_closed_at_bar_close():
    from experiments.strategy_baseline import htf_window

    hour = [{"time": t} for t in range(0, 10 * 3600, 3600)]
    opens = [c["time"] for c in hour]
    # 5m bar opening 04:55 closes 05:00 -> the 04:00 hour candle has just closed.
    window = htf_window(hour, opens, 3600, bar_close=5 * 3600, size=3)
    assert [c["time"] for c in window] == [2 * 3600, 3 * 3600, 4 * 3600]
    # One second earlier the 04:00 candle is still forming.
    window = htf_window(hour, opens, 3600, bar_close=5 * 3600 - 1, size=3)
    assert window[-1]["time"] == 3 * 3600


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

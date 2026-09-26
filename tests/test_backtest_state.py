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

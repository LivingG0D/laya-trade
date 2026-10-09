import asyncio

import pytest

import src.auto_trader as auto_trader_module
from src.auto_trader import AutoTrader
from src.simulated_exchange import STARTING_BALANCE, SimulatedExchange
from tests.test_auto_trader_multi import FakeLayaEngine, FakeMarketService, FakeSignalGenerator


class Prices:
    """A price source the test moves by hand."""

    def __init__(self, value=0.0):
        self.value = value

    async def __call__(self, symbol):
        return self.value


def run(coro):
    return asyncio.run(coro)


def test_position_without_bracket_gets_the_fallback_bracket():
    exchange = SimulatedExchange()
    position = exchange.simulate_position("BTCUSDT.P", "LONG", quantity=1, entry_price=100.0, leverage=10)
    assert (position["stopLoss"], position["takeProfit1"], position["takeProfit2"]) == pytest.approx((97.5, 104.0, 106.0))
    assert position["displaySymbol"] == "BTCUSDT.P" and position["symbol"] == "BTC-SWAP-USDT"


def test_marks_follow_the_price_source_and_brackets_fill_like_the_backtest():
    prices = Prices(100.0)
    exchange = SimulatedExchange(price_source=prices)
    run(exchange.create_order("BTCUSDT.P", "BUY", quantity=2.0, leverage=10, stop_loss=98.0,
                              take_profit_1=102.0, take_profit_2=104.0))

    prices.value = 101.0
    [position] = run(exchange.get_positions())
    assert position["unrealizedPnl"] == pytest.approx(2.0)

    prices.value = 102.5  # TP1: half closes at the TP1 price
    [position] = run(exchange.get_positions())
    assert position["quantity"] == pytest.approx(1.0) and position["tp1Filled"]
    assert exchange.balance == pytest.approx(STARTING_BALANCE + 2.0)

    prices.value = 97.0  # gapped through the stop: fills at the observed price
    assert run(exchange.get_positions()) == []
    assert exchange.balance == pytest.approx(STARTING_BALANCE + 2.0 - 3.0)


def test_flash_close_reports_the_whole_trade_including_partial_fills():
    prices = Prices(100.0)
    exchange = SimulatedExchange(price_source=prices)
    exchange.simulate_position("ETHUSDT.P", "SHORT", quantity=2.0, entry_price=100.0, leverage=10,
                               stop_loss=102.0, take_profit_1=98.0, take_profit_2=95.0)
    prices.value = 97.5
    run(exchange.get_positions())  # TP1 on half: +2

    result = run(exchange.flash_close("ETH-SWAP-USDT"))

    assert result["success"] and result["realizedPnl"] == pytest.approx(2.0 + 2.5)
    assert run(exchange.flash_close("ETHUSDT.P"))["success"] is False


def test_balance_reserves_margin_and_counts_open_pnl():
    exchange = SimulatedExchange()
    exchange.simulate_position("BTCUSDT.P", "LONG", quantity=1.0, entry_price=100.0, leverage=10)
    run(exchange.get_positions(current_market_price=101.0))
    balance = run(exchange.get_balance())
    assert balance["availableBalance"] == pytest.approx(STARTING_BALANCE - 10.0)
    assert balance["totalEquity"] == pytest.approx(STARTING_BALANCE + 1.0)


def test_auto_trader_trades_end_to_end_on_the_simulated_exchange(tmp_path, monkeypatch):
    """Entry, TP1 partial, break-even, stop-out and booking, with no mocked exchange."""
    monkeypatch.setattr(auto_trader_module, "STATE_FILE", tmp_path / "state.json")
    prices = Prices()
    exchange = SimulatedExchange(price_source=prices)
    trader = AutoTrader(exchange=exchange, market_service=FakeMarketService(), laya_engine=FakeLayaEngine(),
                        signal_generator=FakeSignalGenerator())
    trader.settings.update(mode="demo", shadow_mode=False, symbols=["BTCUSDT.P"], max_positions=1,
                           breakeven_mode="legacy", opposing_reversal_exit=False)
    assert run(trader.toggle(True))["enabled"]

    run(trader.tick())  # opens the long the fake generator plans: stop -2, TP1 +3, TP2 +5
    [position] = exchange.positions.values()
    entry = position["entryPrice"]
    assert position["stopLoss"] == pytest.approx(entry - 2.0)

    prices.value = entry + 3.1  # TP1 fills half on the exchange; the trader moves the stop to break-even
    run(trader.tick())
    assert trader.get_active_trade("BTCUSDT.P")["be_moved"]
    assert exchange.positions["BTC-SWAP-USDT"]["stopLoss"] == pytest.approx(entry * 1.0005)

    prices.value = entry  # back through the break-even stop: the exchange closes the rest
    run(trader.tick())
    assert exchange.positions == {}
    assert trader.get_active_trade("BTCUSDT.P") is None
    assert trader.stats["closed_trades"] == 1 and trader.stats["winning_trades"] == 1
    assert exchange.balance > STARTING_BALANCE

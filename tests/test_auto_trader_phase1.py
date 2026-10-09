import asyncio
import json
import time

import pytest

import src.auto_trader as auto_trader_module
from tests.test_auto_trader_multi import make_trader


def candidate(entry=100.0, stop=99.0, tp1=101.2, action="LONG", confidence=80.0):
    return {
        "symbol": "BTCUSDT.P",
        "signal": {
            "action": action, "confidence_pct": confidence, "entry_price": entry,
            "stop_loss": stop, "take_profit_1": tp1, "take_profit_2": 0.0, "max_hold_seconds": 7200,
        },
        "latest": {"price": entry},
    }


def position(mark=100.0, pnl_pct=0.0, side="LONG"):
    return {
        "symbol": "BTC-SWAP-USDT", "rawSymbol": "BTC-SWAP-USDT", "displaySymbol": "BTCUSDT.P",
        "side": side, "entryPrice": 100.0, "markPrice": mark, "quantity": 1.0,
        "pnlPercentage": pnl_pct, "unrealizedPnl": 0.0,
    }


def test_risk_pct_sizing_loses_the_configured_share_at_the_stop(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch, shadow=False)
    trader.settings.update(sizing_mode="risk_pct", equity_risk_pct=1.0, leverage=10)

    asyncio.run(trader._execute_candidate(candidate(entry=100.0, stop=99.0)))

    # 1% of $100 equity at a 1% stop -> $100 notional -> 1.0 contract of $100
    assert client.create_order.await_args.kwargs["quantity"] == pytest.approx(1.0)


EXPIRED = {
    "symbol": "BTCUSDT.P", "exchange_symbol": "BTC-SWAP-USDT", "side": "LONG", "entry_price": 100.0,
    "stop_loss": 99.4, "take_profit_1": 101.2, "take_profit_2": 0.0, "be_moved": False,
    "max_hold_seconds": 7200,
}


def test_time_stop_closes_positions_held_past_their_limit(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    trader.enabled = True
    trader._set_active_trade(dict(EXPIRED, open_time=time.time() - 7300))

    asyncio.run(trader.tick())

    client.flash_close.assert_awaited_once_with("BTC-SWAP-USDT")
    assert trader.get_active_trade("BTCUSDT.P") is None


def test_time_stop_still_runs_when_trader_is_disabled_or_halted(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    trader.enabled = False
    trader.status = "HALTED"
    trader._set_active_trade(dict(EXPIRED, open_time=time.time() - 7300))

    asyncio.run(trader.tick())

    client.flash_close.assert_awaited_once()


def test_failed_time_stop_is_logged_and_protection_keeps_running(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    client.flash_close.return_value = {"success": False, "message": "rejected"}
    trader._set_active_trade(dict(EXPIRED, open_time=time.time() - 7300))

    asyncio.run(trader._enforce_time_stops())
    asyncio.run(trader._manage_position(position()))

    assert any(log["category"] == "TIME_STOP" and log["level"] == "danger" for log in trader.logs)
    assert trader.get_active_trade("BTCUSDT.P") is not None
    assert trader._analysis_results["BTCUSDT.P"]["state"] == "READY"  # supervision still ran


def test_time_stop_skips_trades_within_their_limit(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    trader._set_active_trade(dict(EXPIRED, open_time=time.time() - 60))

    asyncio.run(trader._enforce_time_stops())

    client.flash_close.assert_not_awaited()


def test_breakeven_tp1_fees_moves_only_on_tp1_and_covers_costs(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    trader.settings.update(breakeven_mode="tp1_fees", opposing_reversal_exit=False, mode="demo")
    trade = {
        "symbol": "BTCUSDT.P", "exchange_symbol": "BTC-SWAP-USDT", "side": "LONG", "entry_price": 100.0,
        "stop_loss": 99.0, "take_profit_1": 102.0, "take_profit_2": 104.0, "be_moved": False,
        "open_time": time.time(),
    }
    trader._set_active_trade(dict(trade))

    asyncio.run(trader._manage_position(position(mark=100.5, pnl_pct=25.0)))  # big ROE, no TP1
    stops = [call.kwargs["stop_loss"] for call in client.set_position_tpsl.await_args_list]
    assert not any(stop > 100 for stop in stops)

    asyncio.run(trader._manage_position(position(mark=102.1, pnl_pct=100.0)))  # TP1 reached
    assert client.set_position_tpsl.await_args.kwargs["stop_loss"] == pytest.approx(100.12)


def write_table(tmp_path, monkeypatch, mean_r):
    table_dir = tmp_path / "edge_tables"
    table_dir.mkdir()
    (table_dir / "gate.json").write_text(json.dumps({
        "BTCUSDT.P": {"min_samples": 30, "stats": {"LONG:80": {"n": 50, "mean_r": mean_r}}},
    }))
    monkeypatch.setattr(auto_trader_module, "EDGE_TABLE_DIR", table_dir)


def test_edge_gate_keeps_only_buckets_with_positive_measured_r(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch, symbols=["BTCUSDT.P"])
    write_table(tmp_path, monkeypatch, mean_r=0.12)
    trader.settings.update(edge_gate=True, edge_table_file="gate.json", min_confidence=85.0)

    kept = asyncio.run(trader._scan_candidates([]))
    assert [c["symbol"] for c in kept] == ["BTCUSDT.P"]  # gate replaces the 85% threshold
    assert kept[0]["signal"]["expected_r"] == pytest.approx(0.12)


def test_edge_gate_rejects_negative_or_unknown_buckets(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch, symbols=["BTCUSDT.P", "ETHUSDT.P"])
    write_table(tmp_path, monkeypatch, mean_r=-0.05)
    trader.settings.update(edge_gate=True, edge_table_file="gate.json")

    assert asyncio.run(trader._scan_candidates([])) == []


def test_edge_table_file_setting_cannot_escape_the_table_directory(tmp_path, monkeypatch):
    trader, _ = make_trader(tmp_path, monkeypatch)
    asyncio.run(trader.update_settings({"edge_table_file": "../../.env"}))
    assert trader.settings["edge_table_file"] == ".env"


def test_edge_table_reloads_when_file_changes_or_appears(tmp_path, monkeypatch):
    import os

    trader, _ = make_trader(tmp_path, monkeypatch, symbols=["BTCUSDT.P"])
    table_dir = tmp_path / "edge_tables"
    table_dir.mkdir()
    monkeypatch.setattr(auto_trader_module, "EDGE_TABLE_DIR", table_dir)
    trader.settings.update(edge_gate=True, edge_table_file="gate.json")

    assert trader._edge_table("BTCUSDT.P") is None  # missing file
    assert any("gate.json" in log["message"] for log in trader.logs)

    path = table_dir / "gate.json"
    path.write_text(json.dumps({"BTCUSDT.P": {"min_samples": 30, "stats": {"LONG:80": {"n": 50, "mean_r": 0.1}}}}))
    assert trader._edge_table("BTCUSDT.P").expected_r({"action": "LONG", "confidence_pct": 80}) == pytest.approx(0.1)

    path.write_text(json.dumps({"BTCUSDT.P": {"min_samples": 30, "stats": {"LONG:80": {"n": 50, "mean_r": -0.2}}}}))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert trader._edge_table("BTCUSDT.P").expected_r({"action": "LONG", "confidence_pct": 80}) == pytest.approx(-0.2)


def test_mode_mismatch_halt_still_applies_the_whole_settings_update(tmp_path, monkeypatch):
    trader, client = make_trader(tmp_path, monkeypatch)
    trader.enabled = True
    client.is_demo = False  # configured demo, connected live: the update must halt

    result = asyncio.run(trader.update_settings({
        "auto_breakeven": False, "max_daily_loss_usd": 3.0, "cooldown_seconds": 45,
    }))

    assert result["success"] is False and trader.status == "HALTED"
    assert trader.settings["auto_breakeven"] is False
    assert trader.settings["max_daily_loss_usd"] == 3.0
    assert trader.settings["cooldown_seconds"] == 45

import asyncio
import sys
import os

# Add root directory to pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding='utf-8')

from src.toobit_client import ToobitClient, generate_position_suggestion
from src.market_data import to_toobit_symbol


async def run_tests():
    print("=== TEST 1: ToobitClient Initialization & Demo Mode ===")
    client = ToobitClient(is_demo=True)
    assert client.is_connected, "Client should be marked connected in demo mode"
    balance = await client.get_balance()
    print("Demo Balance:", balance)
    assert balance["availableBalance"] > 0, "Available balance should be > 0"

    print("\n=== TEST 2: Demo Positions Querying & Real-Time Mark Calculation ===")
    positions = await client.get_positions(current_market_price=0.004300)
    print(f"Active positions count: {len(positions)}")
    assert len(positions) == 1, "Default demo position should be present"
    pos = positions[0]
    print("Position details:", pos)
    assert pos["side"] == "LONG"
    assert pos["unrealizedPnl"] > 0, "At 0.004300 with 0.00418 entry, Long should be in profit"

    print("\n=== TEST 3: AI Position Suggestion Generation ===")
    mock_laya = {
        "primary_action": "STRONG_LONG",
        "confidence": 85,
        "probabilities": {
            "strong_long": 0.60,
            "scalp_long": 0.25,
            "neutral": 0.10,
            "scalp_short": 0.04,
            "strong_short": 0.01
        }
    }
    mock_signal = {
        "action": "LONG",
        "confidence": 85,
        "targets": {
            "entry": 0.00418,
            "stop_loss": 0.00412,
            "take_profit_1": 0.00445,
            "take_profit_2": 0.00460
        }
    }
    # Test A: Holding LONG + Bullish market
    sug_aligned = generate_position_suggestion(pos, 0.004300, mock_signal, mock_laya)
    print("Suggestion (Aligned):", sug_aligned["badgeText"], "|", sug_aligned["headline"])
    assert sug_aligned["alignment"] == "ALIGNED"
    assert "HOLD" in sug_aligned["action"]

    # Test B: Holding LONG + Bearish market reversal
    mock_laya_bear = {
        "primary_action": "STRONG_SHORT",
        "confidence": 82,
        "probabilities": {
            "strong_long": 0.02,
            "scalp_long": 0.08,
            "neutral": 0.10,
            "scalp_short": 0.35,
            "strong_short": 0.45
        }
    }
    mock_signal_bear = {
        "action": "SHORT",
        "confidence": 82,
        "targets": {
            "entry": 0.00430,
            "stop_loss": 0.00438,
            "take_profit_1": 0.00410
        }
    }
    sug_opposed = generate_position_suggestion(pos, 0.004300, mock_signal_bear, mock_laya_bear)
    print("Suggestion (Opposed / In Profit):", sug_opposed["badgeText"], "|", sug_opposed["headline"])
    assert sug_opposed["alignment"] == "OPPOSED"
    assert sug_opposed["action"] == "TAKE_PROFIT"
    assert sug_opposed["canFlashClose"] is True

    # Test C: No position
    sug_none = generate_position_suggestion(None, 0.004300, mock_signal, mock_laya)
    print("Suggestion (No Position):", sug_none["badgeText"], "|", sug_none["headline"])
    assert sug_none["hasPosition"] is False
    assert sug_none["action"] == "ENTER_SETUP"

    print("\n=== TEST 4: Flash Close Execution ===")
    close_res = await client.flash_close("1000PEPEUSDT.P")
    print("Flash Close Result:", close_res)
    assert close_res["success"] is True

    remaining = await client.get_positions()
    print("Positions after close:", len(remaining))
    assert len(remaining) == 0, "Position should be closed"

    print("\n=== TEST 5: Demo Simulation (Opening Short) ===")
    sim_pos = client.simulate_position("1000PEPEUSDT.P", "SHORT", 200000, 0.00435, leverage=10)
    print("Simulated Short:", sim_pos)
    assert sim_pos["side"] == "SHORT"
    assert sim_pos["quantity"] == 200000

    print("\n=== TEST 6: HMAC Signature Generation Verification ===")
    auth_client = ToobitClient("my_api_key", "my_api_secret", is_demo=False)
    test_params = {"symbol": "1000PEPE-SWAP-USDT", "timestamp": 1700000000000}
    sig = auth_client._generate_signature(test_params)
    print(f"Generated HMAC SHA-256 signature ({len(sig)} chars): {sig[:16]}...")
    assert len(sig) == 64, "HMAC-SHA256 signature should be 64 hex characters"

    # Regression guard: Toobit signs the exact query string that is sent, in the
    # order it is sent (never sorted). Verified against the documented example at
    # https://api-docs.toobit.com/api/spot-example.html
    doc_secret = "30lfjDT51iOG1kYZnDoLNynOyMdIcmQyO1XYfxzYOmQfx9tjiI98Pzio4uhZ0Uk2"
    doc_params = {
        "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": 1, "price": 400, "recvWindow": 100000, "timestamp": 1668481902307,
    }
    doc_client = ToobitClient("k", doc_secret, is_demo=False)
    doc_sig = doc_client._generate_signature(doc_params)
    assert doc_sig == "8420e499e71cce4a00946db16543198b6bcae01791bdb75a06b5a7098b156468", (
        "Signature must match Toobit's documented vector - params are signed in "
        f"sent order, not sorted. Got {doc_sig}"
    )
    print("Signature matches Toobit documented vector (order-preserving):", doc_sig[:16], "...")
    await doc_client.close()

    await client.close()
    await auth_client.close()
    print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")


if __name__ == "__main__":
    asyncio.run(run_tests())

import asyncio, os, sys, time
sys.path.insert(0, '.')
from src.toobit_client import ToobitClient
from src.config import TOOBIT_API_KEY, TOOBIT_API_SECRET

async def main():
    client = ToobitClient(api_key=TOOBIT_API_KEY, api_secret=TOOBIT_API_SECRET, is_demo=False)
    await client.sync_server_time()
    
    symbol = "BTC-SWAP-USDT"
    sl_price = "84784.7"
    tp_price = "84025.4"
    
    print("\n--- 1. Testing /api/v1/futures/position/trading-stop ---")
    for side_val in ["SHORT", "SELL", "BUY_CLOSE", "SELL_CLOSE"]:
        ts_params = {
            "symbol": symbol,
            "side": side_val,
            "stopLoss": sl_price,
            "takeProfit": tp_price
        }
        try:
            res = await client._send_signed_request("POST", "/api/v1/futures/position/trading-stop", ts_params)
            print(f"SUCCESS with side={side_val}:", res)
            await client.close()
            return
        except Exception as e:
            print(f"FAILED with side={side_val}:", e)
            
    print("\n--- 2. Testing /api/v1/futures/order with STOP type & stopPrice ---")
    # For a SHORT position, the SL order to close is BUY_CLOSE
    sl_params = {
        "symbol": symbol,
        "side": "BUY_CLOSE",
        "type": "STOP",
        "priceType": "MARKET",
        "quantity": "2.6",
        "stopPrice": sl_price,
        "newClientOrderId": f"sl_{int(time.time() * 1000)}"
    }
    try:
        res = await client._send_signed_request("POST", "/api/v1/futures/order", sl_params)
        print("SUCCESS placing SL order via /api/v1/futures/order:", res)
    except Exception as e:
        print("FAILED placing SL order:", e)

    tp_params = {
        "symbol": symbol,
        "side": "BUY_CLOSE",
        "type": "STOP",
        "priceType": "MARKET",
        "quantity": "2.6",
        "stopPrice": tp_price,
        "newClientOrderId": f"tp_{int(time.time() * 1000)}"
    }
    try:
        res = await client._send_signed_request("POST", "/api/v1/futures/order", tp_params)
        print("SUCCESS placing TP order via /api/v1/futures/order:", res)
    except Exception as e:
        print("FAILED placing TP order:", e)

    await client.close()

asyncio.run(main())

import asyncio
import hashlib
import hmac
import time
import httpx

async def test_toobit_signing():
    async with httpx.AsyncClient() as client:
        # Check server time
        r = await client.get("https://api.toobit.com/api/v1/time")
        server_time = r.json()["serverTime"]
        local_time = int(time.time() * 1000)
        time_offset = server_time - local_time
        print(f"Server time: {server_time}, Local time: {local_time}, Offset: {time_offset}ms")

        # Let's test with a simulated key and secret
        api_key = "test_key_64_chars_length_padding_for_format_validation_check_12345"
        secret = "test_secret_64_chars_length_padding_for_format_validation_check"

        params = {
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000) + time_offset
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        
        headers = {
            "X-BB-APIKEY": api_key,
            "User-Agent": "TooBit-SDK/1.0.0"
        }
        url = f"https://api.toobit.com/api/v1/futures/balance?{qs}&signature={sig}"
        resp = await client.get(url, headers=headers)
        print("Test with 64-char key status:", resp.status_code, resp.text)

asyncio.run(test_toobit_signing())

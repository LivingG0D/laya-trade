"""
Test AutoTrader REST API endpoints.
"""

import asyncio
import httpx
from src.app import app

async def test_api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Test status
        r = await client.get("/api/autotrade/status")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data
        assert "settings" in data
        assert "stats" in data
        assert "logs" in data
        print("✅ Status API: 200 OK")

        # 2. Test toggle ON
        r = await client.post("/api/autotrade/toggle", json={"enabled": True})
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["enabled"] is True
        print("✅ Toggle ON API: 200 OK")

        # 3. Test settings update
        r = await client.post("/api/autotrade/settings", json={"settings": {"fixed_margin_usd": 2.0, "leverage": 50}})
        assert r.status_code == 200
        data = r.json()
        assert data["settings"]["fixed_margin_usd"] == 2.0
        assert data["settings"]["leverage"] == 50
        print("✅ Settings API: 200 OK")

        # 4. Test emergency stop
        r = await client.post("/api/autotrade/emergency_stop")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        print("✅ Emergency Stop API: 200 OK")

        # 5. Test clear logs
        r = await client.post("/api/autotrade/clear_logs")
        assert r.status_code == 200
        print("✅ Clear Logs API: 200 OK")

        # Ensure disabled after emergency stop
        r = await client.get("/api/autotrade/status")
        assert r.json()["enabled"] is False

    print("\nALL API ENDPOINTS VERIFIED 100% OPERATIONAL!")

if __name__ == "__main__":
    asyncio.run(test_api())

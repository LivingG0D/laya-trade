import urllib.request
import json
import time

time.sleep(1)

req = urllib.request.urlopen("http://127.0.0.1:8000/api/status")
status = json.loads(req.read().decode())
print("=== API STATUS ===")
for k, v in status.items():
    if k != "supported_symbols":
        print(f"{k}: {v}")

print("\n=== TRIGGERING /api/analyze ===")
t0 = time.time()
req2 = urllib.request.urlopen("http://127.0.0.1:8000/api/analyze?symbol=BTCUSDT.P&interval=5m")
lat = (time.time() - t0) * 1000
res = json.loads(req2.read().decode())
print(f"Response received in {lat:.1f}ms")
print(f"Signal Action: {res['signal']['action']} ({res['signal']['signal_type']})")
print(f"Laya Engine: {res['laya']['engine']}")
print(f"Laya Probabilities: {res['laya']['probabilities']}")
print(f"Laya Latency: {res['laya']['latency_ms']} ms")
print(f"Position: {res['laya']['position']}")
print(f"Confidence Score: {res['laya']['confidence_score']}")

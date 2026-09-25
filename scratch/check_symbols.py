import urllib.request
import json

for sym in ["ETHUSDT.P", "SOLUSDT.P", "DOGEUSDT.P"]:
    url = f"http://127.0.0.1:8000/api/analyze?symbol={sym}&interval=5m"
    req = urllib.request.urlopen(url)
    res = json.loads(req.read().decode())
    action = res["signal"]["action"]
    engine = res["laya"]["engine"]
    lat = res["laya"]["latency_ms"]
    probs = res["laya"]["probabilities"]
    print(f"Symbol: {sym} -> Action: {action} | Engine: {engine} | Latency: {lat}ms | Probs: {probs}")

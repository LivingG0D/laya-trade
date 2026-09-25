import urllib.request
import json
import time

req = urllib.request.urlopen("http://127.0.0.1:8000/api/symbols")
data = json.loads(req.read().decode())
symbols = data.get("symbols", [])

print("=" * 75)
print(f"SCANNING ALL {len(symbols)} SUPPORTED SYMBOLS FOR LIVE SIGNALS")
print("=" * 75)

actions = {}
for sym in symbols[:15]:
    try:
        url = f"http://127.0.0.1:8000/api/analyze?symbol={sym}&interval=5m"
        r = urllib.request.urlopen(url)
        res = json.loads(r.read().decode())
        action = res["signal"]["action"]
        sig_type = res["signal"]["signal_type"]
        conv = res["signal"]["confidence_pct"]
        reasons = res["signal"].get("reasons", [])
        
        actions[action] = actions.get(action, 0) + 1
        
        reasons_str = "; ".join(reasons[:2]) if reasons else ""
        print(f"  {sym:<16} -> {action:<8} ({sig_type:<12}) | Conv: {conv:5.1f}% | {reasons_str}")
    except Exception as e:
        print(f"  {sym:<16} -> ERROR: {e}")

print("=" * 75)
print("Action counts:", actions)

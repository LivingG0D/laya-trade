import urllib.request
import json

req = urllib.request.urlopen("http://127.0.0.1:8000/api/symbols")
symbols = json.loads(req.read().decode())["symbols"]

print(f"Testing {len(symbols)} symbols...")
for sym in symbols:
    try:
        url = f"http://127.0.0.1:8000/api/analyze?symbol={sym}&interval=5m"
        res = json.loads(urllib.request.urlopen(url).read().decode())
        sig = res["signal"]
        laya = res["laya"]
        p_l = laya["long_prob"]
        p_s = laya["short_prob"]
        p_h = laya["hold_prob"]
        dir_conv = laya.get("directional_conviction", 0)
        action = sig["action"]
        sig_type = sig["signal_type"]
        reasons = sig.get("reasons", [])
        first_reason = reasons[0] if reasons else "None"
        print(f"{sym:<15} | Act: {action:<7} ({sig_type:<12}) | L: {p_l:.3f} S: {p_s:.3f} H: {p_h:.3f} | Conv: {dir_conv:.3f} | {first_reason[:60]}")
    except Exception as e:
        print(f"{sym:<15} | ERROR: {e}")

import sys

path = r"C:\Users\danie\.gemini\antigravity\brain\a62b8ea2-6e10-430e-a737-30e8e556db99\.system_generated\steps\1255\content.md"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find("/api/v1/futures/balance")
while idx != -1:
    print("--- MATCH AT", idx, "---")
    chunk = text[max(0, idx - 200):min(len(text), idx + 800)]
    sys.stdout.buffer.write(chunk.encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(b"\n\n")
    idx = text.find("/api/v1/futures/balance", idx + 1)

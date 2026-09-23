import re

path = r"C:\Users\danie\.gemini\antigravity\brain\a62b8ea2-6e10-430e-a737-30e8e556db99\.system_generated\steps\1255\content.md"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

endpoints = set(re.findall(r"/api/v[12]/[a-zA-Z0-9_/]+", text))
print("Found", len(endpoints), "endpoints:")
for ep in sorted(endpoints):
    print(ep)

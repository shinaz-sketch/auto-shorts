"""Diagnostic: Pollinations size/model variants, one request per 47s (free limit: 1 image / 45s per IP)."""
import time, requests

P = "blocky plastic roblox style 3D character standing on a quiet night street, cinematic lighting, vertical"
q = requests.utils.quote(P)
variants = [
    ("720x1280 no model", "width=720&height=1280&nologo=true"),
    ("576x1024 flux", "width=576&height=1024&nologo=true&model=flux"),
    ("720x1280 flux", "width=720&height=1280&nologo=true&model=flux"),
    ("540x960 no model", "width=540&height=960&nologo=true"),
]
for i, (label, qs) in enumerate(variants):
    if i:
        time.sleep(47)
    s = time.time()
    try:
        r = requests.get(f"https://image.pollinations.ai/prompt/{q}?{qs}&seed={i+11}", timeout=120)
        ct = r.headers.get("content-type", "")
        print(f"{label}: HTTP {r.status_code} {ct[:18]} {len(r.content)}B {'' if ct.startswith('image') else r.text[:140]} [{time.time()-s:.0f}s]", flush=True)
    except Exception as e:
        print(f"{label}: EXC {str(e)[:120]}", flush=True)

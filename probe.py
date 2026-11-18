"""Quick diagnostic: which free image services work from GitHub's servers right now?"""
import os, time, json, base64, requests

def t(label, fn):
    s = time.time()
    try:
        print(f"{label}: {fn()} [{time.time()-s:.0f}s]", flush=True)
    except Exception as e:
        print(f"{label}: EXC {str(e)[:150]} [{time.time()-s:.0f}s]", flush=True)

P = "blocky plastic roblox style 3D character standing on a night street, cinematic lighting"
def poll(url):
    def f():
        r = requests.get(url, timeout=90)
        return f"HTTP {r.status_code} {r.headers.get('content-type','')[:20]} {len(r.content)}B {r.text[:120] if not r.headers.get('content-type','').startswith('image') else ''}"
    return f

q = requests.utils.quote(P)
t("pollinations default small", poll(f"https://image.pollinations.ai/prompt/{q}?width=360&height=640&nologo=true"))
t("pollinations flux 720x1280", poll(f"https://image.pollinations.ai/prompt/{q}?width=720&height=1280&nologo=true&model=flux"))
t("pollinations turbo 720x1280", poll(f"https://image.pollinations.ai/prompt/{q}?width=720&height=1280&nologo=true&model=turbo"))
t("pollinations 2nd request (rate limit check)", poll(f"https://image.pollinations.ai/prompt/{q}?width=720&height=1280&nologo=true&seed=7"))
t("pollinations new gen host", poll(f"https://gen.pollinations.ai/image/{q}?width=720&height=1280&nologo=true"))

key = os.environ.get("GEMINI_API_KEY", "")
if key:
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 200}, timeout=30)
        names = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        imgs = [n for n in names if "image" in n]
        print("gemini image-capable models:", imgs, flush=True)
        for m in imgs[:3]:
            def g(m=m):
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", params={"key": key}, timeout=120,
                                  json={"contents": [{"parts": [{"text": "Generate an image: " + P + ", vertical 9:16"}]}], "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}})
                ok = r.status_code == 200 and any("inlineData" in p for c in r.json().get("candidates", []) for p in c.get("content", {}).get("parts", []))
                return f"HTTP {r.status_code} image_returned={ok} {'' if ok else r.text[:160]}"
            t("gemini " + m, g)
    except Exception as e:
        print("gemini probe failed", str(e)[:150])

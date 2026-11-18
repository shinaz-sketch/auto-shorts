"""Diagnostic: which free voice services work from GitHub's servers (with hard timeouts)?"""
import asyncio, time, requests, subprocess, os

TEXT = "At exactly midnight, a child disappeared from the quiet street."

async def edge(voice, boundary):
    import edge_tts
    c = edge_tts.Communicate(TEXT, voice, boundary=boundary)
    n = b = 0
    async for ch in c.stream():
        if ch["type"] == "audio": b += len(ch["data"])
        elif ch["type"] == "WordBoundary": n += 1
    return f"audio {b}B, word events {n}"

for voice, boundary in [("en-US-GuyNeural", "WordBoundary"), ("en-US-GuyNeural", "SentenceBoundary")]:
    s = time.time()
    try:
        print(f"edge-tts {voice} {boundary}:", asyncio.run(asyncio.wait_for(edge(voice, boundary), 40)), f"[{time.time()-s:.0f}s]", flush=True)
    except BaseException as e:
        print(f"edge-tts {voice} {boundary}: FAIL {type(e).__name__} {str(e)[:150]} [{time.time()-s:.0f}s]", flush=True)

for v in ["Brian", "Matthew", "Joey"]:
    s = time.time()
    try:
        r = requests.get("https://api.streamelements.com/kappa/v2/speech", params={"voice": v, "text": TEXT}, timeout=30)
        print(f"streamelements {v}: HTTP {r.status_code} {r.headers.get('content-type','')[:20]} {len(r.content)}B [{time.time()-s:.0f}s]", flush=True)
    except Exception as e:
        print(f"streamelements {v}: FAIL {str(e)[:100]}", flush=True)

try:
    subprocess.run(["pip", "install", "-q", "gTTS"], check=True, capture_output=True)
    from gtts import gTTS
    s = time.time()
    gTTS(TEXT, lang="en", tld="com").save("g.mp3")
    print(f"gTTS: ok {os.path.getsize('g.mp3')}B [{time.time()-s:.0f}s]", flush=True)
except Exception as e:
    print("gTTS: FAIL", str(e)[:120], flush=True)

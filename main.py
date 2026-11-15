import os, json, random, time, subprocess, asyncio, pathlib, datetime, shutil, requests
import edge_tts
import yt

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "state.json"
GENRES = ["horror", "revenge", "mystery", "sci-fi twist", "moral story", "crime", "romance twist", "survival"]
VOICES = ["en-US-GuyNeural", "en-US-AriaNeural", "en-GB-RyanNeural", "en-US-ChristopherNeural"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
KEY = os.environ["GEMINI_API_KEY"]


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"videos": []}


def save_state(s):
    STATE.write_text(json.dumps(s, indent=1))


# ---------- learning from performance ----------
def refresh_stats(s):
    ids = [v["id"] for v in s["videos"][-50:]]
    if not ids:
        return
    st = yt.stats(ids)
    now = time.time()
    for v in s["videos"]:
        if v["id"] in st:
            v["views"] = st[v["id"]]["views"]
            v["likes"] = st[v["id"]]["likes"]
            age_h = max((now - v["ts"]) / 3600, 1)
            v["vph"] = v["views"] / age_h if age_h >= 6 else None


def genre_scores(s):
    sc = {}
    for g in GENRES:
        vs = [v["vph"] for v in s["videos"] if v["genre"] == g and v.get("vph") is not None]
        sc[g] = (sum(vs) / len(vs)) if vs else None
    return sc


def pick_genre(s):
    sc = genre_scores(s)
    untested = [g for g, x in sc.items() if x is None]
    if untested:
        return random.choice(untested)
    if random.random() < 0.25:  # keep exploring
        return random.choice(GENRES)
    total = sum(sc.values()) or 1
    r, acc = random.uniform(0, total), 0
    for g, x in sc.items():
        acc += x
        if r <= acc:
            return g
    return GENRES[0]


def pick_voice(s):
    best = {}
    for v in s["videos"]:
        if v.get("vph") is not None:
            best.setdefault(v["voice"], []).append(v["vph"])
    if best and random.random() > 0.3:
        return max(best, key=lambda k: sum(best[k]) / len(best[k]))
    return random.choice(VOICES)


# ---------- story ----------
def llm(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
    r = requests.post(url, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}}, timeout=120)
    r.raise_for_status()
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def make_story(s, genre):
    top = sorted([v for v in s["videos"] if v.get("vph")], key=lambda v: -v["vph"])[:3]
    recent = [v["title"] for v in s["videos"][-25:]]
    prompt = f"""Write an original short story for a YouTube Short (45-55 seconds of narration, about 120-140 words).
Genre: {genre}.
Rules: the FIRST sentence must be a shocking hook that makes people stop scrolling. Fast pacing, simple words,
a twist near the end, and a last line that loops back to the start or leaves a cliffhanger so people rewatch.
Split into 7-9 scenes. Each scene has "narration" (1-2 short sentences) and "image_prompt" (a vivid English visual
description, no text in the image, no real people or brands).
Best performing titles so far (make something in the same spirit, never copy): {[v['title'] for v in top]}
Do NOT reuse these recent titles: {recent}
Return JSON: {{"title": "...(max 70 chars, curiosity-driven)", "description": "...", "tags": ["..."], "scenes": [{{"narration": "...", "image_prompt": "..."}}]}}"""
    return llm(prompt)


# ---------- media ----------
async def tts(text, voice, mp3):
    c = edge_tts.Communicate(text, voice, rate="+8%", boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return words


def ts(t):
    return f"{int(t//3600)}:{int(t%3600//60):02d}:{t%60:05.2f}"


def make_ass(words, path, dur):
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: D,DejaVu Sans,92,&H0000FFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,2,5,60,60,0,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for i in range(0, len(words), 3):
        ch = words[i:i + 3]
        start = ch[0][0]
        end = words[i + 3][0] if i + 3 < len(words) else dur
        text = " ".join(w[2] for w in ch).upper().replace("\n", " ")
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},D,,0,0,0,,{text}")
    pathlib.Path(path).write_text(head + "\n".join(lines))


def get_image(prompt, path, seed):
    q = requests.utils.quote(prompt + ", cinematic, dramatic lighting, vertical composition, highly detailed")
    for i in range(4):
        try:
            r = requests.get(f"https://image.pollinations.ai/prompt/{q}?width=1080&height=1920&nologo=true&seed={seed}", timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                pathlib.Path(path).write_bytes(r.content)
                return
        except Exception:
            pass
        time.sleep(6 * (i + 1))
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x14142b:s=1080x1920", "-frames:v", "1", str(path)],
                   check=True, capture_output=True)


def duration(f):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def build_video(story, voice, work):
    work.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, sc in enumerate(story["scenes"]):
        mp3, img, ass, out = f"a{i}.mp3", f"i{i}.jpg", f"s{i}.ass", f"c{i}.mp4"
        words = asyncio.run(tts(sc["narration"], voice, str(work / mp3)))
        dur = duration(str(work / mp3)) + 0.2
        make_ass(words, work / ass, dur)
        get_image(sc["image_prompt"], work / img, random.randint(1, 10**6))
        zoom = random.choice(["min(zoom+0.0009,1.3)", "if(eq(on,0),1.3,max(zoom-0.0009,1.0))"])
        vf = (f"scale=1620:2880,zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={int(dur*30)+10}:s=1080x1920:fps=30,ass={ass},format=yuv420p")
        subprocess.run(["ffmpeg", "-y", "-i", img, "-i", mp3, "-vf", vf, "-af", "apad", "-t", f"{dur:.2f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2", out],
                       cwd=work, check=True, capture_output=True)
        clips.append(out)
    (work / "list.txt").write_text("".join(f"file '{c}'\n" for c in clips))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "final.mp4"],
                   cwd=work, check=True, capture_output=True)
    return work / "final.mp4"


def run_once():
    s = load_state()
    try:
        refresh_stats(s)
    except Exception as e:
        print("stats skipped:", e)
    genre, voice = pick_genre(s), pick_voice(s)
    story = make_story(s, genre)
    work = ROOT / "work"
    shutil.rmtree(work, ignore_errors=True)
    video = build_video(story, voice, work)
    desc = story["description"] + "\n\n#shorts #story #ai"
    vid = yt.upload(str(video), story["title"], desc, story.get("tags", []) + ["shorts"])
    s["videos"].append({"id": vid, "title": story["title"], "genre": genre, "voice": voice,
                        "ts": time.time(), "views": 0, "likes": 0, "vph": None})
    save_state(s)
    print("uploaded", vid, genre, voice)


if __name__ == "__main__":
    for attempt in range(3):
        try:
            run_once()
            break
        except Exception as e:
            print("attempt failed:", repr(e))
            time.sleep(30)
    else:
        raise SystemExit(1)

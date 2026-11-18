"""Auto Shorts agent: Roblox-style 3D story Shorts, fully automatic.

Flow per run: learn from stats -> write story (Gemini) -> images (Pollinations) -> voice (edge-tts)
-> fast-cut video + word-by-word captions (ffmpeg) -> thumbnail -> upload -> save state.
Long stories are split into Part 1 / Part 2 across consecutive runs.
"""
import os, re, json, random, time, subprocess, asyncio, pathlib, shutil

import requests

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "state.json"
W, H, FPS = 720, 1280, 30
API = "https://generativelanguage.googleapis.com/v1beta"
PRIVACY = os.environ.get("UPLOAD_PRIVACY", "public")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

GENRES = [
    "creepy midnight mystery in a quiet neighborhood",
    "haunted school after dark",
    "a strange place that nobody is allowed to enter",
    "an urban legend that turns out to be real",
    "a time loop that gets worse every night",
    "a secret room discovered inside a house",
    "a mysterious stranger and a gift with a dark twist",
    "a revenge story with a shocking twist",
    "survival in an abandoned place",
    "a friend who is not who they seem",
]
VOICES = ["en-US-ChristopherNeural", "en-US-GuyNeural", "en-GB-RyanNeural", "en-US-EricNeural", "en-US-AriaNeural"]
STYLE = ("Roblox game style 3D render, blocky plastic avatar characters with simple cartoon faces, "
         "cinematic lighting, vivid saturated colors, shallow depth of field, highly detailed, "
         "vertical 9:16 composition, no text, no watermark")


def load_state():
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    s.setdefault("videos", [])
    s.setdefault("series", None)
    return s


def save_state(s):
    STATE.write_text(json.dumps(s, indent=1))


def run(cmd, cwd=None, timeout=900):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-800:]}")
    return r.stdout


def probe(f):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).strip())


# ---------------------------------------------------------------- learning
def refresh_stats(s):
    import yt
    ids = [v["id"] for v in s["videos"][-50:]]
    if not ids:
        return
    st = yt.stats(ids)
    now = time.time()
    for v in s["videos"]:
        if v["id"] in st:
            v["views"], v["likes"] = st[v["id"]]["views"], st[v["id"]]["likes"]
            age_h = max((now - v["ts"]) / 3600, 1)
            v["vph"] = v["views"] / age_h if age_h >= 6 else None


def pick_genre(s):
    sc = {}
    for g in GENRES:
        vs = [v["vph"] for v in s["videos"] if v.get("genre") == g and v.get("vph") is not None]
        sc[g] = sum(vs) / len(vs) if vs else None
    untested = [g for g, x in sc.items() if x is None]
    if untested:
        return random.choice(untested)
    if random.random() < 0.25:
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


# ---------------------------------------------------------------- LLM
def _extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _scrub(msg):
    key = os.environ.get("GEMINI_API_KEY", "")
    return str(msg).replace(key, "***") if key else str(msg)


def gemini_models():
    """Ask Google which models exist right now, newest stable flash first (model names get retired)."""
    r = requests.get(f"{API}/models", params={"key": os.environ["GEMINI_API_KEY"], "pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    bad = ("image", "tts", "live", "audio", "embed", "robotics", "computer", "vision", "gemma",
           "learnlm", "aqa", "imagen", "veo", "research", "native")
    cands = [n for n in names if "flash" in n and not any(b in n for b in bad)]

    def key(n):
        m = re.search(r"(\d+(?:\.\d+)?)", n)
        return (("preview" in n or "exp" in n), -(float(m.group(1)) if m else 0), "lite" in n)
    return sorted(cands, key=key)


COMMON = """You write viral YouTube Shorts horror/mystery stories narrated by an AI voice and shown as fast-cut
Roblox-style 3D animated scenes (blocky plastic avatars, cinematic lighting).
RULES:
- The FIRST sentence of scene 1 is a shocking hook that makes people stop scrolling (a time, a number, a disappearance, a warning).
- Simple spoken English, short punchy sentences, present or past tense, no filler. Every scene is 1-2 short sentences (8-18 words).
- Keep tension rising scene by scene. Include one or two creepy details with exact numbers or times.
- Last line is short and chilling, ideally echoing the hook so the video loops.
- Make the main characters consistent: give each a short visual description (hair, clothes colors) in "characters".
- "image_prompt" for each scene: describe ONE vivid camera shot (who, where, lighting, mood, camera angle). English only,
  no text or captions in the image, no real people, no brands, nothing gory. Mention the character look every time they appear.
Return ONLY JSON:
{"title": "curiosity-driven title, max 70 chars, no hashtags",
 "description": "2 sentence teaser",
 "tags": ["8 short tags"],
 "hook_text": "2-4 word shocking thumbnail text in capitals",
 "summary": "3 sentence summary of everything that happens",
 "characters": "visual description of each main character",
 "setting": "visual description of the main location and color mood",
 "scenes": [{"narration": "...", "image_prompt": "..."}]}"""


def llm(prompt):
    last = None
    models = gemini_models()
    forced = os.environ.get("GEMINI_MODEL")
    if forced:
        models.insert(0, forced)
    models = models[:6] or ["gemini-flash-latest"]
    print("models to try:", models)
    for model in models:
        url = f"{API}/models/{model}:generateContent"
        for attempt in range(3):
            try:
                r = requests.post(url, params={"key": os.environ["GEMINI_API_KEY"]}, timeout=240, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}})
            except Exception as e:
                last = f"{model}: {_scrub(e)[:200]}"
                time.sleep(10)
                continue
            if r.status_code == 200:
                try:
                    out = _extract_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                    print("story model:", model)
                    return out
                except Exception as e:
                    last = f"{model}: unreadable answer {_scrub(e)[:150]}"
                    break
            last = f"{model}: HTTP {r.status_code} {_scrub(r.text)[:200]}"
            print(last)
            if r.status_code in (429, 500, 503):
                time.sleep(20 * (attempt + 1))
                continue
            break
    raise RuntimeError("no Gemini model worked. last: " + str(last))


def make_story(s, genre, part, series):
    top = sorted([v for v in s["videos"] if v.get("vph")], key=lambda v: -v["vph"])[:3]
    recent = [v["title"] for v in s["videos"][-25:]]
    hints = (f"Best performing titles so far (same spirit, never copy): {[v['title'] for v in top]}\n" if top else "")
    n = random.randint(12, 14)
    if part == 2:
        task = (f"Write PART 2 (the final part) of this story. Keep the same characters and setting.\n"
                f"Title: {series['title']}\nCharacters: {series['characters']}\nSetting: {series['setting']}\n"
                f"What happened in Part 1: {series['summary']}\n"
                f"Scene 1 must immediately continue the cliffhanger. Deliver the payoff, then end with a final "
                f"twist and one chilling short last line. Use exactly {n} scenes.")
    else:
        if part == 1:
            task = (f"Write PART 1 of a 2-part story about: {genre}. Use exactly {n} scenes. End on a huge "
                    f"cliffhanger reveal that is NOT resolved, final line teasing what comes next.")
        else:
            task = (f"Write a complete single story about: {genre}. Use exactly {n + 2} scenes. End with a twist "
                    f"that changes the meaning of the story, last line short and chilling.")
    story = llm(f"{COMMON}\n\n{task}\n{hints}Do NOT reuse these recent titles: {recent}")
    sc = [x for x in story.get("scenes", []) if x.get("narration") and x.get("image_prompt")]
    if len(sc) < 8:
        raise RuntimeError("story too short")
    story["scenes"] = sc
    if part == 2:
        story["title"], story["characters"], story["setting"] = series["title"], series["characters"], series["setting"]
    return story


# ---------------------------------------------------------------- media
async def _tts(text, voice, mp3):
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate="+6%", boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return words


def _gtts(text, mp3):
    from gtts import gTTS
    gTTS(text, lang="en", tld="com").save(mp3)
    return []  # no word timings: captions are spread evenly


def tts_scene(text, voice, mp3):
    last = None
    for v in (voice, voice, "en-US-GuyNeural"):
        try:
            words = asyncio.run(asyncio.wait_for(_tts(text, v, mp3), 50))
            if os.path.getsize(mp3) > 1000:
                return words
        except BaseException as e:
            last = e
            time.sleep(2)
    print(f"edge-tts failed ({type(last).__name__}), using backup voice", flush=True)
    return _gtts(text, mp3)


_last_image_at = 0.0
IMAGE_GAP = 46  # free image service: 1 image / 45 s per IP


def fetch_image(prompt, path, seed):
    global _last_image_at
    q = requests.utils.quote(prompt[:1200])
    for i in range(6):
        wait = _last_image_at + IMAGE_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true"
                             f"&seed={seed}&model=flux", timeout=170)
            if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                pathlib.Path(path).write_bytes(r.content)
                _last_image_at = time.time()
                return True
            print(f"image http {r.status_code}: {r.text[:90]!r}", flush=True)
            _last_image_at = time.time() if r.status_code == 402 else _last_image_at
            time.sleep(12 if r.status_code in (500, 502, 503, 504) else 3)
        except Exception as e:
            print("image error:", str(e)[:100], flush=True)
            time.sleep(8)
    return False


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(words, path):
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
            "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
            "MarginV,Encoding\n"
            "Style: W,DejaVu Sans,68,&H001FFFE8,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,3,2,40,40,"
            "330,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    out = []
    for i, (a, b, t) in enumerate(words):
        t = re.sub(r"[,.;:\"“”]+$", "", t.strip()).replace("{", "").replace("}", "")
        if not t:
            continue
        end = words[i + 1][0] if i + 1 < len(words) else b
        end = max(end, a + 0.12)
        out.append(f"Dialogue: 0,{ass_time(a)},{ass_time(end)},W,,0,0,0,,"
                   r"{\fscx85\fscy85\t(0,90,\fscx112\fscy112)\t(90,170,\fscx100\fscy100)}" + t)
    pathlib.Path(path).write_text(head + "\n".join(out) + "\n")


def effect_expr(name, n):
    c = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return {
        "in": f"z='1+0.22*on/{n}':{c}",
        "out": f"z='1.22-0.22*on/{n}':{c}",
        "left": f"z='1.2':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",
        "right": f"z='1.2':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
        "face": f"z='1.08+0.32*on/{n}':x='iw/2-(iw/zoom/2)':y='max(0,ih*0.3-ih/zoom/2)'",
    }[name]


def encode_shot(img, frames, effect, out, cwd):
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
          f"zoompan={effect_expr(effect, frames)}:d={frames}:s={W}x{H}:fps={FPS},"
          "eq=contrast=1.06:saturation=1.12,format=yuv420p")
    run(["ffmpeg", "-y", "-i", img, "-vf", vf, "-frames:v", frames, "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-r", FPS, out], cwd)


def build_video(story, voice, work):
    work.mkdir(parents=True, exist_ok=True)
    scenes = story["scenes"]
    prefix = (f"{STYLE}. Setting: {story.get('setting', '')}. "
              f"Characters: {story.get('characters', '')[:380]}. Shot: ")

    t0, oks = time.time(), []
    for i in range(len(scenes)):
        if time.time() - t0 > 1500:  # time budget: reuse nearby images for the rest
            oks.append(False)
            continue
        oks.append(fetch_image(prefix + scenes[i]["image_prompt"], work / f"i{i}.jpg", random.randint(1, 10**6)))
        print(f"image {i + 1}/{len(scenes)} {'ok' if oks[-1] else 'FAILED'} ({time.time() - t0:.0f}s)", flush=True)
    if sum(oks) < 0.6 * len(scenes):
        raise RuntimeError(f"image service failed ({sum(oks)}/{len(scenes)} images)")
    good = [i for i, ok in enumerate(oks) if ok]
    for i, ok in enumerate(oks):  # reuse the nearest good image for any failed one
        if not ok:
            src = min(good, key=lambda g: abs(g - i))
            shutil.copy(work / f"i{src}.jpg", work / f"i{i}.jpg")

    cum, prev_end, shots, words_all, wavs = 0.0, 0, [], [], []
    seq, last_fx = ["in", "out", "left", "right", "face"], None
    for i, sc in enumerate(scenes):
        mp3, wav = work / f"a{i}.mp3", work / f"a{i}.wav"
        words = tts_scene(sc["narration"], voice, str(mp3))
        run(["ffmpeg", "-y", "-i", mp3, "-af", "apad=pad_dur=0.1", "-ar", 44100, "-ac", 2, wav])
        dur = probe(str(wav))
        if not words:  # no timing from the voice service: spread the words evenly
            toks = sc["narration"].split()
            step = dur / max(len(toks), 1)
            words = [(k * step, (k + 1) * step, t) for k, t in enumerate(toks)]
        words_all += [(cum + a, cum + b, t) for a, b, t in words]
        cum += dur
        end_frame = round(cum * FPS)
        nfr = end_frame - prev_end
        n = max(1, min(round(dur / 2.3), nfr // 10))
        base = nfr // n
        for k in range(n):
            frames = base if k < n - 1 else nfr - base * (n - 1)
            fx = "face" if not shots else random.choice([e for e in seq if e != last_fx])
            last_fx = fx
            shots.append((f"i{i}.jpg", frames, fx))
        prev_end = end_frame
        wavs.append(wav)
    total = cum
    print(f'voice done: {total:.0f}s of narration, {len(shots)} shots', flush=True)
    if total > 175:
        raise RuntimeError(f"video too long: {total:.0f}s")

    t1 = time.time()
    for k, (img, frames, fx) in enumerate(shots):
        encode_shot(img, frames, fx, f"c{k}.mp4", work)
    print(f'shots encoded in {time.time() - t1:.0f}s', flush=True)
    (work / "v.txt").write_text("".join(f"file 'c{k}.mp4'\n" for k in range(len(shots))))
    (work / "a.txt").write_text("".join(f"file '{w.name}'\n" for w in wavs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", 0, "-i", "v.txt", "-c", "copy", "video.mp4"], work)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", 0, "-i", "a.txt", "-c", "copy", "narration.wav"], work)
    make_ass(words_all, work / "cap.ass")
    print("mixing final video", flush=True)

    drone = ("aevalsrc=0.5*sin(2*PI*55*t)+0.3*sin(2*PI*82.4*t)*(0.6+0.4*sin(2*PI*0.2*t))"
             f"+0.15*sin(2*PI*110*t):s=44100:d={total:.2f}")
    fc = (f"[0:v]ass=cap.ass[v];[2:a]volume=0.12,afade=t=in:d=2,afade=t=out:st={max(total - 2, 0):.2f}:d=2[m];"
          "[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    run(["ffmpeg", "-y", "-i", "video.mp4", "-i", "narration.wav", "-f", "lavfi", "-i", drone,
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart",
         "final.mp4"], work)
    return work / "final.mp4"


def make_thumbnail(img_path, hook, part, out):
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
    im = Image.open(img_path).convert("RGB")
    sc = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * sc), int(im.height * sc)))
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.15)).enhance(1.25)
    d = ImageDraw.Draw(im, "RGBA")
    for y in range(H // 2, H):  # dark fade at the bottom for text contrast
        d.line([(0, y), (W, y)], fill=(0, 0, 0, int(190 * (y - H // 2) / (H // 2))))
    try:
        f = ImageFont.truetype(FONT, 110)
        fs = ImageFont.truetype(FONT, 56)
    except Exception:
        f = fs = ImageFont.load_default()
    words, lines, cur = (hook or "WATCH THIS").upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= W - 80:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    y = H - 140 - 125 * len(lines)
    for ln in lines:
        x = (W - d.textlength(ln, font=f)) / 2
        d.text((x, y), ln, font=f, fill=(255, 232, 31), stroke_width=9, stroke_fill=(0, 0, 0))
        y += 125
    if part:
        d.rounded_rectangle([30, 40, 270, 120], 18, fill=(214, 28, 40))
        d.text((55, 52), f"PART {part}", font=fs, fill=(255, 255, 255))
    im.save(out, "JPEG", quality=88)


# ---------------------------------------------------------------- main
def dry_run():
    """Build a whole video with placeholder pictures (no uploads, no image service) to test the machine."""
    from PIL import Image, ImageDraw

    def fake(prompt, path, seed):
        random.seed(seed)
        im = Image.new("RGB", (W, H), tuple(random.randint(30, 200) for _ in range(3)))
        d = ImageDraw.Draw(im)
        for _ in range(10):
            x, y = random.randint(0, W - 150), random.randint(0, H - 200)
            d.rectangle([x, y, x + 140, y + 190], fill=tuple(random.randint(0, 255) for _ in range(3)))
        im.save(path)
        return True
    globals()["fetch_image"] = fake
    lines = ["At exactly twelve o'clock at night, a child vanished from my street.",
             "Nobody saw anything, but every house heard the same music.",
             "It was the ice cream truck, and it was not supposed to exist anymore.",
             "I followed the sound with my phone light shaking in my hand.",
             "The truck was parked under a broken streetlight, glowing bright pink.",
             "The driver smiled at me and said, you are just in time."]
    story = {"setting": "quiet night street", "characters": "boy in a blue jacket",
             "scenes": [{"narration": lines[i % len(lines)], "image_prompt": "x"} for i in range(14)]}
    work = ROOT / "work"
    shutil.rmtree(work, ignore_errors=True)
    t = time.time()
    build_video(story, "en-US-GuyNeural", work)
    make_thumbnail(work / "i1.jpg", "MISSING AT MIDNIGHT", 1, work / "thumb.jpg")
    print(f"DRY RUN OK in {time.time() - t:.0f}s", flush=True)


def run_once():
    import yt
    s = load_state()
    try:
        refresh_stats(s)
    except Exception as e:
        print("stats skipped:", _scrub(e))
    series = s.get("series")
    if series:
        part, genre, voice = 2, series["genre"], series["voice"]
    else:
        part = 1 if random.random() < 0.5 else 0
        genre, voice = pick_genre(s), pick_voice(s)
    print(f"genre={genre!r} voice={voice} part={part or 'single'}")
    story = make_story(s, genre, part, series)
    work = ROOT / "work"
    shutil.rmtree(work, ignore_errors=True)
    video = build_video(story, voice, work)
    label = f" (Part {part})" if part else ""
    title = (story["title"][:80] + label)[:95]
    tail = "\n\nPart 2 coming next!" if part == 1 else ""
    desc = f"{story.get('description', '')}{tail}\n\n#shorts #storytime #scarystories #animation"
    tags = [t.lstrip("#") for t in story.get("tags", [])][:12] + ["shorts", "storytime"]
    vid = yt.upload(str(video), title, desc, tags, PRIVACY)
    print("uploaded", vid, PRIVACY)
    try:
        thumb = work / "thumb.jpg"
        make_thumbnail(work / ("i1.jpg" if len(story["scenes"]) > 1 else "i0.jpg"), story.get("hook_text"),
                       part or None, thumb)
        yt.set_thumbnail(vid, str(thumb))
        print("thumbnail set")
    except Exception as e:
        print("thumbnail skipped:", _scrub(e)[:300])
    s["videos"].append({"id": vid, "title": title, "genre": genre, "voice": voice, "part": part or None,
                        "ts": time.time(), "views": 0, "likes": 0, "vph": None})
    if part == 1:
        s["series"] = {"title": story["title"], "summary": story.get("summary", ""), "genre": genre,
                       "voice": voice, "characters": story.get("characters", ""), "setting": story.get("setting", "")}
    else:
        s["series"] = None
    save_state(s)


if __name__ == "__main__":
    if os.environ.get("DRY_RUN"):
        dry_run()
        raise SystemExit(0)
    try:
        import yt
        print("YouTube channel:", yt.check(), flush=True)
    except Exception as e:
        print("YOUTUBE LOGIN PROBLEM:", _scrub(e)[:300], flush=True)
        raise SystemExit(2)
    for attempt in range(2):
        try:
            run_once()
            break
        except Exception as e:
            print("attempt failed:", _scrub(repr(e))[:1500])
            time.sleep(30)
    else:
        raise SystemExit(1)

# Auto Shorts Agent (₹0, runs in the cloud, phone not needed)

Pipeline every run: pick best-performing genre/voice -> Gemini writes story -> edge-tts voice + word captions
-> Pollinations AI images with zoom motion -> ffmpeg -> upload to YouTube Shorts -> save stats to state.json.
GitHub Actions runs it 3x/day (08:30, 13:30, 19:00 IST), even if you're offline for months.

## Setup (all free, doable from phone browser except step 3)
1. GitHub: create a **public** repo (public = unlimited free Actions minutes). Upload these files.
   Move `shorts.yml` to `.github/workflows/shorts.yml`.
2. Gemini key: aistudio.google.com -> Get API key.
3. YouTube API: console.cloud.google.com -> new project -> enable "YouTube Data API v3" -> OAuth consent screen
   -> set to **In production** (if left on "Testing", the token dies in 7 days) -> create OAuth client (Desktop app)
   -> download client_secret.json -> run get_token.py once (Google Colab works) and copy the 3 values.
4. Repo -> Settings -> Secrets and variables -> Actions -> add: GEMINI_API_KEY, YT_CLIENT_ID,
   YT_CLIENT_SECRET, YT_REFRESH_TOKEN.
5. Actions tab -> shorts -> Run workflow to test.

## Important
- New API projects: YouTube locks API uploads to PRIVATE until you pass the API compliance audit
  (form: support.google.com/youtube/contact/yt_api_form). Apply on day 1.
- YouTube demonetizes "mass-produced / repetitive" content. Vary stories, keep quality, label as AI (already set).
- GitHub pauses scheduled workflows after 60 days of repo inactivity; the state.json commits normally keep it alive,
  but open the repo once a month to be safe.

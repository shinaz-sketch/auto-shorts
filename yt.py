import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def svc():
    c = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                    scopes=SCOPES)
    return build("youtube", "v3", credentials=c, cache_discovery=False)


def check():
    """Fail fast: prove the stored YouTube login works and return the channel name."""
    for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        if not os.environ.get(k):
            raise RuntimeError(f"GitHub secret {k} is empty or missing")
    items = svc().channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("this Google login has no YouTube channel")
    return items[0]["snippet"]["title"]


def upload(path, title, desc, tags, privacy="public"):
    body = {
        "snippet": {"title": title[:95], "description": desc[:4900], "tags": tags[:15], "categoryId": "24"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    req = svc().videos().insert(part="snippet,status", body=body,
                                media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=3)
    return resp["id"]


def set_thumbnail(video_id, jpg):
    svc().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(jpg, mimetype="image/jpeg")).execute()


def stats(ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = svc().videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            st = it["statistics"]
            out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0))}
    return out

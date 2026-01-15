import os
import json
import hmac
import hashlib
import time
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any

import requests
from fastapi import FastAPI, Header, HTTPException, Body, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================
# Config
# =========================

BASE_DIR = Path(__file__).resolve().parent           # api/
SONGS_PATH = BASE_DIR / "songs.json"                # api/songs.json

CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # В Railway Variables должен быть ADMIN_TOKEN
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")  # для реальной проверки initData (можно пустым для dev)

WEB_ORIGINS = [
    "https://sincere-perception-production-65ac.up.railway.app",
    "https://web.telegram.org",
    "http://localhost:3000",
]

# week_id -> list of dict songs
SONGS_BY_WEEK: Dict[int, List[Dict[str, Any]]] = {}

# voting storage (in-memory)
VOTES: Dict[int, Dict[int, int]] = {}         # week_id -> song_id -> count
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}  # week_id -> user_id -> [song_id,...]


# =========================
# Models
# =========================

class WeekOut(BaseModel):
    id: int
    title: str
    status: Literal["open", "closed"]


class SongOut(BaseModel):
    id: int
    artist: str
    title: str
    is_new: bool = False
    weeks_in_chart: Optional[int] = None
    cover: Optional[str] = None
    preview_url: Optional[str] = None
    source: Optional[str] = "manual"


class VoteIn(BaseModel):
    song_ids: List[int] = []


class VoteOut(BaseModel):
    ok: bool
    week_id: int
    user_id: str
    voted_song_ids: List[int]


# =========================
# App
# =========================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# Helpers
# =========================

def get_current_week() -> Dict[str, Any]:
    return {"id": CURRENT_WEEK_ID, "title": f"Week {CURRENT_WEEK_ID}", "status": "open"}


def ensure_week_exists(week_id: int) -> None:
    if week_id != CURRENT_WEEK_ID:
        raise HTTPException(status_code=404, detail="WEEK_NOT_FOUND")


def load_songs_from_file() -> List[Dict[str, Any]]:
    """Reads api/songs.json. Expected format: JSON array (list of songs dicts)."""
    if not SONGS_PATH.exists():
        print(f"[BOOT] songs.json NOT FOUND: {SONGS_PATH}", flush=True)
        return []

    try:
        raw = json.loads(SONGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            print(f"[BOOT] songs.json is not a list, got: {type(raw)}", flush=True)
            return []
        print(f"[BOOT] songs.json loaded: {len(raw)} items", flush=True)
        return raw
    except Exception as e:
        print(f"[BOOT] songs.json FAILED to load: {e}", flush=True)
        return []


def save_songs_to_file(items: List[Dict[str, Any]]) -> None:
    """Writes back to api/songs.json (pretty)."""
    SONGS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN_NOT_SET_ON_SERVER")
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="ADMIN_UNAUTHORIZED")


def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    """
    Dev-friendly:
    - If init_data is empty -> "dev"
    Real check:
    - If TG_BOT_TOKEN set and init_data provided -> verify signature
    """
    if not init_data:
        return "dev"

    # If no bot token, accept any non-empty init_data (dev mode)
    if not TG_BOT_TOKEN:
        return "dev"

    # Parse initData querystring
    # init_data looks like "query_id=...&user=...&auth_date=...&hash=..."
    parts = init_data.split("&")
    data = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            data[k] = v

    recv_hash = data.get("hash")
    if not recv_hash:
        return "dev"

    # Build check string (sorted, excluding hash)
    check_items = []
    for k in sorted(data.keys()):
        if k == "hash":
            continue
        check_items.append(f"{k}={data[k]}")
    check_string = "\n".join(check_items)

    secret_key = hashlib.sha256(TG_BOT_TOKEN.encode("utf-8")).digest()
    calc_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, recv_hash):
        raise HTTPException(status_code=401, detail="BAD_INITDATA_SIGNATURE")

    # Extract user id from "user" json if present (urlencoded json usually)
    # For MVP: return a stable id
    return data.get("user", "tg_user")


def itunes_search_track(artist: str, title: str) -> Optional[Dict[str, str]]:
    """
    Uses iTunes Search API (public).
    Returns: {"cover": "...", "preview_url": "..."} or None
    """
    q = f"{artist} {title}".strip()
    if not q:
        return None

    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": q, "entity": "song", "limit": 1},
            timeout=10,
            headers={"User-Agent": "deerzone-chart/1.0"},
        )
        r.raise_for_status()
        js = r.json()
        results = js.get("results", [])
        if not results:
            return None

        item = results[0]
        cover = item.get("artworkUrl100")
        # try upscale cover a bit if present
        if isinstance(cover, str):
            cover = cover.replace("100x100bb.jpg", "600x600bb.jpg")

        preview = item.get("previewUrl")

        out = {"cover": cover, "preview_url": preview}
        return out
    except Exception as e:
        print(f"[ITUNES] error: {e}", flush=True)
        return None


def normalize_songs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure each song has id and required fields."""
    out: List[Dict[str, Any]] = []
    next_id = 1
    for s in items:
        if not isinstance(s, dict):
            continue
        s2 = dict(s)

        if "id" not in s2:
            s2["id"] = next_id
        try:
            s2["id"] = int(s2["id"])
        except Exception:
            s2["id"] = next_id

        next_id = max(next_id, s2["id"] + 1)

        if "artist" not in s2:
            s2["artist"] = ""
        if "title" not in s2:
            s2["title"] = ""

        if "is_new" not in s2:
            s2["is_new"] = False
        if "source" not in s2:
            s2["source"] = "manual"

        out.append(s2)

    # reassign IDs if duplicates
    seen = set()
    for s in out:
        if s["id"] in seen:
            s["id"] = next_id
            next_id += 1
        seen.add(s["id"])
    return out


@app.on_event("startup")
def startup_event():
    items = load_songs_from_file()
    items = normalize_songs(items)
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items

    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(SONGS_BY_WEEK.get(CURRENT_WEEK_ID, []))}", flush=True)


# =========================
# Routes
# =========================

@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/__version")
def __version():
    return {"week_id": CURRENT_WEEK_ID, "songs": len(SONGS_BY_WEEK.get(CURRENT_WEEK_ID, []))}


@app.get("/weeks/current", response_model=WeekOut)
def weeks_current():
    w = get_current_week()
    return WeekOut(id=w["id"], title=w["title"], status=w["status"])


@app.get("/weeks/{week_id}/songs", response_model=List[SongOut])
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    # auth (в Mini App initData есть; для браузера/PS допускаем пустое)
    try:
        _ = user_id_from_telegram_init_data(x_telegram_init_data)
    except Exception:
        pass

def ensure_week_exists(week_id: int):
    # если неделя уже загружена в память — ничего не делаем
    if week_id in SONGS_BY_WEEK and isinstance(SONGS_BY_WEEK[week_id], list) and len(SONGS_BY_WEEK[week_id]) > 0:
        return

    # если ещё не загружена — подгружаем из файла и кладём в эту неделю
    items = load_songs_from_file()
    items = normalize_songs(items)

    SONGS_BY_WEEK[week_id] = items
    print(f"[BOOT/ENSURE] week_id={week_id} loaded={len(items)}", flush=True)

    # ✅ ВАЖНО: берём только из SONGS_BY_WEEK
    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    if filter == "new":
        items = [s for s in items if getattr(s, "is_new", False)]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in (f"{s.artist} {s.title}".lower())]

    return items


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int):
    ensure_week_exists(week_id)
    votes = VOTES.get(week_id, {})
    return [{"song_id": sid, "votes": votes.get(sid, 0)} for sid in sorted(votes.keys())]


@app.post("/weeks/{week_id}/vote", response_model=VoteOut)
def weeks_vote(
    week_id: int,
    payload: VoteIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user_id = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(week_id)

    song_ids = [int(x) for x in (payload.song_ids or [])]
    existing = {int(s["id"]) for s in SONGS_BY_WEEK.get(week_id, [])}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(status_code=400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    USER_VOTES.setdefault(week_id, {})
    VOTES.setdefault(week_id, {})

    # overwrite user vote: remove previous
    prev = USER_VOTES[week_id].get(user_id, [])
    for sid in prev:
        VOTES[week_id][sid] = max(0, VOTES[week_id].get(sid, 0) - 1)

    for sid in song_ids:
        VOTES[week_id][sid] = VOTES[week_id].get(sid, 0) + 1

    USER_VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)


@app.post("/admin/weeks/current/songs/bulk")
def admin_add_songs(
    songs: List[Dict[str, Any]] = Body(...),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    week_id = CURRENT_WEEK_ID
    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    # next id
    max_id = 0
    for s in items:
        try:
            max_id = max(max_id, int(s.get("id", 0)))
        except Exception:
            pass
    next_id = max_id + 1

    added = 0
    for s in songs:
        if not isinstance(s, dict):
            continue
        items.append({
            "id": next_id,
            "artist": s.get("artist", ""),
            "title": s.get("title", ""),
            "is_new": bool(s.get("is_new", True)),
            "weeks_in_chart": s.get("weeks_in_chart"),
            "cover": s.get("cover"),
            "preview_url": s.get("preview_url"),
            "source": s.get("source", "manual"),
        })
        next_id += 1
        added += 1

    SONGS_BY_WEEK[week_id] = normalize_songs(items)
    save_songs_to_file(SONGS_BY_WEEK[week_id])

    return {"ok": True, "week_id": week_id, "added": added, "count": len(SONGS_BY_WEEK[week_id])}


@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    week_id = CURRENT_WEEK_ID
    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    updated = 0
    skipped = 0
    processed = 0

    for s in items:
        processed += 1

        artist = s.get("artist", "")
        title = s.get("title", "")

        cover = s.get("cover")
        preview = s.get("preview_url")

        if not force and (cover or preview):
            skipped += 1
            continue

        res = itunes_search_track(artist, title)
        if not res:
            continue

        if not cover and res.get("cover"):
            s["cover"] = res.get("cover")
        if not preview and res.get("preview_url"):
            s["preview_url"] = res.get("preview_url")

        updated += 1

    SONGS_BY_WEEK[week_id] = items
    save_songs_to_file(items)

    return {
        "ok": True,
        "week_id": week_id,
        "processed": processed,
        "updated": updated,
        "skipped": skipped,
    }
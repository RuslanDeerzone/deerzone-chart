import os
import re
import time
from typing import List, Optional, Dict, Literal

import requests
from fastapi import FastAPI, Header, HTTPException, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sincere-perception-production-65ac.up.railway.app",
        "https://web.telegram.org",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fallback на случай, если preflight не перехватился middleware (Railway/прокси/сборка)
@app.options("/{path:path}")
def cors_preflight(path: str):
    return Response(status_code=204)

# =============================================================================
# Models
# =============================================================================

class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    is_new: bool = False

    # новые поля (для обложки и превью)
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


class WeekOut(BaseModel):
    id: int
    title: str
    status: Literal["open", "closed"]


# =============================================================================
# In-memory storage (как у тебя было: без БД)
# =============================================================================

# Текущая неделя
CURRENT_WEEK = WeekOut(id=3, title="Week 1 - 2026", status="open")

# Песни по неделям: week_id -> List[SongOut]
SONGS_BY_WEEK: Dict[int, List[SongOut]] = {
    # пример: 3: [...]
}

# Голоса: week_id -> {song_id: votes}
VOTES: Dict[int, Dict[int, int]] = {}

# Голос пользователя: week_id -> {user_id: [song_ids]}
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}

# авто-инкремент id песен
SONG_ID_SEQ = 1


def next_song_id() -> int:
    global SONG_ID_SEQ
    SONG_ID_SEQ += 1
    return SONG_ID_SEQ - 1


def ensure_week_exists(week_id: int):
    if week_id != CURRENT_WEEK.id and week_id not in SONGS_BY_WEEK:
        # можно допилить историю недель, но для текущих задач достаточно
        raise HTTPException(status_code=404, detail="WEEK_NOT_FOUND")


def get_current_week() -> dict:
    return CURRENT_WEEK.model_dump()


# =============================================================================
# Telegram initData auth (упрощённо)
# =============================================================================

def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    """
    В проде Telegram Mini App присылает initData.
    У тебя уже есть рабочая версия; здесь — безопасная заглушка:
    - если initData отсутствует -> считаем "dev"
    - если есть -> возвращаем стабильно строку пользователя
    """
    if not init_data:
        return "dev"

    # просто чтобы было стабильное значение, не ломая логику
    # (твой реальный валидатор можно вернуть позже)
    return str(abs(hash(init_data)))


# =============================================================================
# ADMIN TOKEN (для опасных эндпоинтов /admin/*)
# =============================================================================

def require_admin(x_admin_token: Optional[str]):
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set")
    if x_admin_token != token:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")


# =============================================================================
# iTunes Search (preview + cover)
# =============================================================================

ITUNES_URL = "https://itunes.apple.com/search"

def _clean_query(s: str) -> str:
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)  # убираем скобки (feat., ремиксы)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def itunes_search_track(artist: str, title: str) -> Optional[dict]:
    q = _clean_query(f"{artist} {title}")
    try:
        r = requests.get(
            ITUNES_URL,
            params={
                "term": q,
                "media": "music",
                "entity": "song",
                "limit": 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("resultCount", 0) < 1:
            return None
        return data["results"][0]
    except Exception:
        return None

def itunes_pick_cover(artwork_url: Optional[str]) -> Optional[str]:
    if not artwork_url:
        return None
    return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/weeks/current", response_model=WeekOut)
def weeks_current():
    return CURRENT_WEEK


@app.get("/weeks/{week_id}/songs", response_model=List[SongOut])
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    # auth (в дев-режиме пустой initData допускается)
    _ = user_id_from_telegram_init_data(x_telegram_init_data)

    ensure_week_exists(week_id)
    items = SONGS_BY_WEEK.get(week_id, [])

    if filter == "new":
        items = [s for s in items if s.is_new]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in (s.artist + " " + s.title).lower()]

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

    if CURRENT_WEEK.status != "open":
        raise HTTPException(status_code=403, detail="VOTING_CLOSED")

    song_ids = payload.song_ids or []
    song_ids = [int(x) for x in song_ids]

    existing = {s.id for s in SONGS_BY_WEEK.get(week_id, [])}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(status_code=400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    if len(song_ids) > 10:
        raise HTTPException(status_code=400, detail="TOO_MANY_SONGS_MAX_10")

    USER_VOTES.setdefault(week_id, {})
    VOTES.setdefault(week_id, {})

    prev = USER_VOTES[week_id].get(user_id, [])
    for sid in prev:
        VOTES[week_id][sid] = max(0, VOTES[week_id].get(sid, 0) - 1)

    for sid in song_ids:
        VOTES[week_id][sid] = VOTES[week_id].get(sid, 0) + 1

    USER_VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)


# =============================================================================
# Admin: bulk add songs to current week
# =============================================================================

from fastapi import Body

@app.post("/admin/weeks/current/songs/bulk")
def admin_add_songs(songs: list = Body(...), x_admin_token: Optional[str] = Header(default=None)):
    # защита токеном (если у тебя уже есть ADMIN_TOKEN в env)
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token and x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="BAD_ADMIN_TOKEN")

    week = get_current_week()
    week_id = week["id"]

    ensure_week_exists(week_id)

    if not isinstance(songs, list):
        raise HTTPException(400, detail="songs must be a list")

    # куда реально читает GET:
    SONGS_BY_WEEK.setdefault(week_id, [])

    # чтобы id не конфликтовали
    existing_ids = [s.id for s in SONGS_BY_WEEK[week_id]]
    next_id = (max(existing_ids) + 1) if existing_ids else 1

    added = []
    for s in songs:
        title = s.get("title")
        artist = s.get("artist")
        if not title or not artist:
            continue

        song = SongOut(
            id=next_id,
            title=title,
            artist=artist,
            is_new=bool(s.get("is_new", True)),
            cover=s.get("cover"),
            preview_url=s.get("preview_url"),
            youtube_url=s.get("youtube_url"),
            source=s.get("source", "manual"),
        )
        SONGS_BY_WEEK[week_id].append(song)
        added.append(song)
        next_id += 1

    return {"week_id": week_id, "count": len(added)}


# =============================================================================
# Admin: enrich current week songs with iTunes preview + cover
# =============================================================================

@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    week = get_current_week()
    week_id = week["id"]
    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    updated = 0

    for s in items:
        if not force and (s.cover or s.preview_url):
            continue

        res = itunes_search_track(s.artist, s.title)
        if not res:
            continue

        new_preview = res.get("previewUrl")
        new_cover = itunes_pick_cover(res.get("artworkUrl100"))

        if force or not s.preview_url:
            s.preview_url = new_preview
        if force or not s.cover:
            s.cover = new_cover

        if new_preview or new_cover:
            updated += 1

    return {"week_id": week_id, "updated": updated, "count": len(items)}

@app.get("/__version")
def __version():
    return {"ok": True, "version": "cors-test-1"}
from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Literal

import requests
from fastapi import FastAPI, Header, HTTPException, Body
from pydantic import BaseModel, Field

app = FastAPI(title="deerzone-chart-api")


# =========================
# Models
# =========================

class WeekOut(BaseModel):
    id: int
    title: str
    status: Literal["open", "closed"] = "open"


class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    is_new: bool = False

    # last fixes: covers + previews + fallback
    cover: Optional[str] = None          # image url
    preview_url: Optional[str] = None    # 30s audio preview url
    youtube_url: Optional[str] = None    # fallback open link
    source: str = "manual"


class VoteIn(BaseModel):
    song_ids: List[int] = Field(default_factory=list)


class VoteOut(BaseModel):
    ok: bool
    week_id: int
    user_id: int
    voted_song_ids: List[int]


# =========================
# In-memory storage
# =========================

CURRENT_WEEK = WeekOut(id=3, title="Week 1 - 2026", status="open")

# week_id -> list[SongOut]
SONGS_BY_WEEK: Dict[int, List[SongOut]] = {}

# week_id -> {song_id: votes}
VOTES: Dict[int, Dict[int, int]] = {}

# week_id -> {user_id: [song_ids]}
USER_VOTES: Dict[int, Dict[int, List[int]]] = {}

_song_id_seq = 1


def next_song_id() -> int:
    global _song_id_seq
    v = _song_id_seq
    _song_id_seq += 1
    return v


def get_current_week() -> WeekOut:
    return CURRENT_WEEK


def ensure_week_exists(week_id: int) -> None:
    if week_id not in SONGS_BY_WEEK:
        SONGS_BY_WEEK[week_id] = []
    if week_id not in VOTES:
        VOTES[week_id] = {}
    if week_id not in USER_VOTES:
        USER_VOTES[week_id] = {}


# =========================
# Telegram auth (dev-friendly)
# =========================

def user_id_from_telegram_init_data(init_data: Optional[str]) -> int:
    """
    У тебя "в дев-режиме пустой initData допускается".
    Поэтому: если initData нет — возвращаем фиктивного пользователя 0.
    """
    if not init_data:
        return 0

    # Нормальный разбор initData можно добавить позже.
    # Сейчас оставляем простую стабильную заглушку:
    # если в init_data есть "user_id=123" — вытащим.
    m = re.search(r"(?:user_id=|\"id\":)(\d+)", init_data)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0
    return 0


# =========================
# Admin auth (optional)
# =========================

def require_admin(x_admin_token: Optional[str]) -> None:
    """
    Если ADMIN_TOKEN не задан — разрешаем (удобно на старте).
    Если задан — требуем header X-Admin-Token.
    """
    admin = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin:
        return
    if not x_admin_token or x_admin_token.strip() != admin:
        raise HTTPException(status_code=401, detail="ADMIN_UNAUTHORIZED")


# =========================
# Apple/iTunes enrich + YouTube fallback
# =========================

def youtube_fallback_url(artist: str, title: str) -> str:
    from urllib.parse import quote_plus
    q = quote_plus(f"{artist} {title}".strip())
    return f"https://www.youtube.com/results?search_query={q}"


def itunes_enrich(artist: str, title: str):
    """
    Возвращает (preview_url, cover_url) или (None, None)
    Используем iTunes Search API:
    - previewUrl (30 сек)
    - artworkUrl100 (обложка)
    """
    from urllib.parse import quote_plus

    q = f"{artist} {title}".strip()
    url = f"https://itunes.apple.com/search?term={quote_plus(q)}&entity=song&limit=1"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None, None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None, None

        item = results[0]
        preview = item.get("previewUrl")

        art = item.get("artworkUrl100") or item.get("artworkUrl60") or item.get("artworkUrl30")
        if art:
            # увеличиваем размер, если строка подходит под шаблон
            art = re.sub(r"/\d+x\d+bb\.", "/600x600bb.", art)
            art = re.sub(r"/\d+x\d+bb-", "/600x600bb-", art)

        return preview, art
    except Exception:
        return None, None


# =========================
# Routes
# =========================

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/weeks/current", response_model=WeekOut)
def weeks_current(
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    _ = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(CURRENT_WEEK.id)
    return CURRENT_WEEK


@app.get("/weeks/{week_id}/songs", response_model=List[SongOut])
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    _ = user_id_from_telegram_init_data(x_telegram_init_data)

    ensure_week_exists(week_id)
    items = SONGS_BY_WEEK[week_id]

    if filter == "new":
        items = [s for s in items if s.is_new]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in (s.artist + " " + s.title).lower()]

    # enrich: add youtube fallback always, and try iTunes for cover/preview if missing
    for s in items:
        if not s.youtube_url:
            s.youtube_url = youtube_fallback_url(s.artist, s.title)

        # only fetch if something missing
        if not s.preview_url or not s.cover:
            preview, cover = itunes_enrich(s.artist, s.title)
            if preview and not s.preview_url:
                s.preview_url = preview
            if cover and not s.cover:
                s.cover = cover

    return items


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int):
    ensure_week_exists(week_id)
    votes = VOTES.get(week_id, {})
    return [{"song_id": sid, "votes": votes.get(sid, 0)} for sid in sorted(votes.keys())]


@app.post("/admin/weeks/current/songs/bulk")
def admin_add_songs(
    songs: list = Body(...),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    require_admin(x_admin_token)

    week = get_current_week()
    ensure_week_exists(week.id)

    if not isinstance(songs, list):
        raise HTTPException(status_code=400, detail="songs must be a list")

    added: List[SongOut] = []

    for raw in songs:
        # raw can be dict-like or already simple
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="each item must be an object {artist,title,...}")

        title = (raw.get("title") or "").strip()
        artist = (raw.get("artist") or "").strip()
        if not title or not artist:
            raise HTTPException(status_code=400, detail="each song must include artist and title")

        s = SongOut(
            id=next_song_id(),
            title=title,
            artist=artist,
            is_new=bool(raw.get("is_new", True)),  # новинки по умолчанию true для bulk
            cover=raw.get("cover"),
            preview_url=raw.get("preview_url"),
            youtube_url=raw.get("youtube_url"),
            source=raw.get("source", "manual"),
        )

        SONGS_BY_WEEK[week.id].append(s)
        added.append(s)

    return {"week_id": week.id, "count": len(added), "added": added}


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

    existing = {s.id for s in SONGS_BY_WEEK[week_id]}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(status_code=400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    # unlimited voting selections (as you wanted)
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
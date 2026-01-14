import os
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
import re
import time
import json
import traceback
from pathlib import Path
from typing import List, Optional, Dict, Literal

import requests
from fastapi import FastAPI, Header, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ===== SONGS STORAGE =====
BASE_DIR = Path(__file__).resolve().parent   # папка api/
SONGS_PATH = BASE_DIR / "songs.json"

SONGS_BY_WEEK: Dict[int, list] = {}

def load_songs_from_file():
    global SONGS_BY_WEEK

    if not SONGS_PATH.exists():
        print(f"SONGS: file not found: {SONGS_PATH}", flush=True)
        SONGS_BY_WEEK = {}
        return

    try:
        raw = json.loads(SONGS_PATH.read_text(encoding="utf-8"))

        # ✅ Вариант 1: файл = список песен -> кладём в текущую неделю
        if isinstance(raw, list):
            week_id = get_current_week()["id"]  # у тебя сейчас 3
            SONGS_BY_WEEK = {week_id: raw}
            print(f"SONGS: loaded list into week {week_id}, count={len(raw)}", flush=True)
            return

        # ✅ Вариант 2: файл = словарь { "3": [..], "4":[..] }
        if isinstance(raw, dict):
            data: Dict[int, list] = {}
            for k, v in raw.items():
                try:
                    wk = int(k)
                except ValueError:
                    continue
                data[wk] = v if isinstance(v, list) else []
            SONGS_BY_WEEK = data
            print(
                f"SONGS: loaded weeks={len(SONGS_BY_WEEK)} total={sum(len(v) for v in SONGS_BY_WEEK.values())}",
                flush=True,
            )
            return

        # ❌ неизвестный формат
        print(f"SONGS: unsupported json format: {type(raw)}", flush=True)
        SONGS_BY_WEEK = {}

    except Exception:
        print("SONGS: FAILED TO LOAD", flush=True)
        print(traceback.format_exc(), flush=True)
        SONGS_BY_WEEK = {}

app = FastAPI()
@app.on_event("startup")
def startup_event():
    load_songs_from_file()
print(f"SONGS_PATH = {SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
print(f"SONGS_WEEKS = {list(SONGS_BY_WEEK.keys())}", flush=True)

# ✅ CORS для WEB + Telegram Mini App
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

import traceback
from fastapi import Body, Header, HTTPException
from typing import Optional

@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    try:
        # 🔐 проверка админ-токена
        require_admin(x_admin_token)

        week = get_current_week()
        week_id = week["id"]
        ensure_week_exists(week_id)

        items = SONGS_BY_WEEK.get(week_id, [])
        if not isinstance(items, list):
            print(f"ENRICH: items is not list, got {type(items)}")
            items = []

        updated = 0
        skipped = 0
        processed = 0

        for s in items:
            processed += 1

            cover = getattr(s, "cover", None)
            preview = getattr(s, "preview_url", None)

            if not force and (cover or preview):
                skipped += 1
                continue

            print(f"ENRICH: trying {s.artist} – {s.title} (force={force})")

            res = itunes_search_track(s.artist, s.title)
            if not res:
                print(f"ENRICH: not found {s.artist} – {s.title}")
                continue

            if not cover:
                s.cover = res.get("cover")
            if not preview:
                s.preview_url = res.get("preview_url")

            updated += 1
            print(f"ENRICH: updated {s.artist} – {s.title}")

        print(
            f"ENRICH DONE: week_id={week_id}, "
            f"processed={processed}, updated={updated}, skipped={skipped}"
        )

        return {
            "ok": True,
            "week_id": week_id,
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
        }

    except Exception as e:
        print("❌ ENRICH FAILED")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# ✅ Fallback на случай, если прокси/сборка не пропускает preflight
@app.options("/{path:path}")
def cors_preflight(path: str, request: Request):
    origin = request.headers.get("origin")
    headers = {
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "content-type,x-telegram-init-data,x-admin-token",
        "Access-Control-Max-Age": "86400",
    }
    if origin in {
        "https://sincere-perception-production-65ac.up.railway.app",
        "https://web.telegram.org",
        "http://localhost:3000",
    }:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    return Response(status_code=204, headers=headers)

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
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set on server")

    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")


# =============================================================================
# iTunes Search (preview + cover)
# =============================================================================

import requests

ITUNES_URL = "https://itunes.apple.com/search"

def itunes_lookup(artist: str, title: str):
    q = f"{artist} {title}".strip()
    params = {
        "term": q,
        "media": "music",
        "entity": "song",
        "limit": 5,
    }
    r = requests.get(ITUNES_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", []) or []
    return results

def best_itunes_match(results: list, artist: str, title: str):
    a = (artist or "").lower().strip()
    t = (title or "").lower().strip()

    def score(item):
        ia = (item.get("artistName") or "").lower()
        it = (item.get("trackName") or "").lower()
        s = 0
        if a and a in ia:
            s += 2
        if t and t in it:
            s += 3
        # бонус за точное совпадение по подстроке
        if ia == a:
            s += 1
        if it == t:
            s += 1
        return s

    results = sorted(results, key=score, reverse=True)
    return results[0] if results else None

def normalize_artwork(url: str, size: int = 600):
    if not url:
        return None
    # iTunes обычно даёт .../100x100bb.jpg → меняем на 600x600bb.jpg
    return re.sub(r"/\d+x\d+bb\.", f"/{size}x{size}bb.", url)

def enrich_song_with_itunes(song):
    # song может быть pydantic-моделью SongOut или dict
    artist = getattr(song, "artist", None) if not isinstance(song, dict) else song.get("artist")
    title = getattr(song, "title", None) if not isinstance(song, dict) else song.get("title")

    results = itunes_lookup(artist or "", title or "")
    best = best_itunes_match(results, artist or "", title or "")
    if not best:
        return False

    cover = normalize_artwork(best.get("artworkUrl100") or best.get("artworkUrl60"))
    preview = best.get("previewUrl")

    # записываем только если нашли
    if isinstance(song, dict):
        if cover and not song.get("cover"):
            song["cover"] = cover
        if preview and not song.get("preview_url"):
            song["preview_url"] = preview
        song["source"] = song.get("source") or "itunes"
    else:
        if cover and not getattr(song, "cover", None):
            setattr(song, "cover", cover)
        if preview and not getattr(song, "preview_url", None):
            setattr(song, "preview_url", preview)
        if not getattr(song, "source", None):
            setattr(song, "source", "itunes")

    return True

"/admin/weeks/current/enrich"
def admin_enrich_current_week(
    x_admin_token: Optional[str] = Header(default=None),
):
    # проверка токена
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")

    week = get_current_week()
    week_id = week["id"]

    # ВАЖНО: используй именно тот контейнер песен, который у тебя реально хранит неделю.
    # У тебя раньше было SONGS_BY_WEEK[week_id] — значит работаем с ним:
    items = SONGS_BY_WEEK.get(week_id, [])

    updated = 0
    tried = 0
    for s in items:
        tried += 1
        try:
            changed = enrich_song_with_itunes(s)
            if changed:
                updated += 1
        except Exception:
            # не валим весь процесс из-за одной песни
            continue

    return {"week_id": week_id, "tried": tried, "updated": updated}

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
    # GET списка песен НЕ должен зависеть от Telegram initData.
    # initData нужен для vote, а здесь — просто логируем (если есть).
    try:
        if x_telegram_init_data:
            _ = user_id_from_telegram_init_data(x_telegram_init_data)
    except Exception as e:
        print("weeks_songs: bad initData (ignored):", repr(e))

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

import traceback

"/admin/weeks/current/songs/enrich"
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    try:
        require_admin(x_admin_token)

        week = get_current_week()
        week_id = week["id"]
        ensure_week_exists(week_id)

        items = SONGS_BY_WEEK.get(week_id, [])
        updated = 0

        for s in items:
            # если force=False — не трогаем уже заполненные
            if not force and (s.cover or s.preview_url):
                continue

            # ВОТ ЗДЕСЬ мы ловим падения iTunes/requests, чтобы не уронить весь enrich
            try:
                res = itunes_search_track(s.artist, s.title)
            except Exception as e:
                print("ENRICH ITEM ERROR:", s.artist, "|", s.title, "|", repr(e))
                continue

            if not res:
                continue

            # заполняем поля
            s.cover = res.get("cover")
            s.preview_url = res.get("preview_url")
            s.source = res.get("source", "itunes")
            updated += 1

        return {"week_id": week_id, "updated": updated, "count": len(items)}

    except Exception:
        print("ENRICH FAILED (full traceback):")
        print(traceback.format_exc())
        raise
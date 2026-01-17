# =========================
# 1) IMPORTS
# =========================
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Dict, List, Literal, Optional, Any
from urllib.parse import parse_qs, unquote

import requests
from fastapi import Body, Header, HTTPException, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================
# 2) CONFIG / CONSTANTS
# =========================
BASE_DIR = Path(__file__).resolve().parent          # api/
SONGS_PATH = BASE_DIR / "songs.json"

CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")          # положи в Railway Variables

# разрешённые origins (добавь свои домены при необходимости)
ALLOWED_ORIGINS = [
    "https://sincere-perception-production-65ac.up.railway.app",
    "https://web.telegram.org",
    "http://localhost:3000",
]

# in-memory storage
SONGS_BY_WEEK: Dict[int, List[dict]] = {}
VOTES: Dict[int, Dict[int, int]] = {}               # week_id -> {song_id: votes}
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}    # week_id -> {user_id: [song_ids]}

CURRENT_WEEK = {"id": CURRENT_WEEK_ID, "title": f"Week {CURRENT_WEEK_ID}", "status": "open"}  # open/closed


# =========================
# 3) HELPERS (SONGS STORAGE "IRON MADE")
# =========================
def _atomic_write_json(path: Path, data: Any) -> None:
    """
    Атомарная запись: пишем во временный файл, потом заменяем.
    Это защищает от "пустого songs.json" при сбое записи/деплое.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=4)
    # ВАЖНО: без BOM. Обычный utf-8.
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def normalize_song(d: dict) -> dict:
    """
    Гарантируем наличие ключей и типы.
    """
    out = dict(d or {})
    # id обязателен — но если вдруг нет, ставим временно (лучше не допускать)
    if "id" not in out:
        out["id"] = 0

    out["artist"] = str(out.get("artist") or "").strip()
    out["title"] = str(out.get("title") or "").strip()

    out["is_new"] = bool(out.get("is_new", False))
    out["weeks_in_chart"] = int(out.get("weeks_in_chart", 1) or 1)

    # cover/preview_url допускают null
    out["cover"] = out.get("cover", None)
    out["preview_url"] = out.get("preview_url", None)

    out["source"] = str(out.get("source") or ("new" if out["is_new"] else "carryover"))
    return out


def normalize_songs(items: Any) -> List[dict]:
    if not isinstance(items, list):
        return []
    normed = [normalize_song(x) for x in items if isinstance(x, dict)]
    # убираем явные пустышки
    normed = [x for x in normed if x["artist"] and x["title"] and int(x["id"]) > 0]
    return normed


def load_songs_from_file() -> List[dict]:
    """
    BOM-safe чтение: utf-8-sig автоматически убирает BOM.
    """
    if not SONGS_PATH.exists():
        print(f"[BOOT] songs.json NOT FOUND: {SONGS_PATH}", flush=True)
        return []
    try:
        raw = SONGS_PATH.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, list):
            print(f"[BOOT] songs.json is not list: {type(data)}", flush=True)
            return []
        data = normalize_songs(data)
        print(f"[BOOT] songs.json loaded OK: {len(data)} items", flush=True)
        return data
    except Exception as e:
        print(f"[BOOT] songs.json FAILED: {e}", flush=True)
        return []


def save_songs_to_file(items: List[dict]) -> None:
    _atomic_write_json(SONGS_PATH, items)


def ensure_week_exists(week_id: int) -> None:
    if week_id != CURRENT_WEEK_ID:
        raise HTTPException(status_code=404, detail="WEEK_NOT_FOUND")


def get_current_week() -> dict:
    return dict(CURRENT_WEEK)


def require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        # если админ-токен не задан — это ошибка конфигурации
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN_NOT_CONFIGURED")
    if (x_admin_token or "") != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="BAD_ADMIN_TOKEN")


def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    """
    Минимально практичный парсер initData:
    - если пусто: dev-user
    - иначе пытаемся вытащить user.id из параметра user=...
    """
    if not init_data:
        return "dev-user"

    try:
        # initData выглядит как querystring: "query_id=...&user=%7B...%7D&auth_date=...&hash=..."
        qs = parse_qs(init_data, keep_blank_values=True)
        if "user" in qs and qs["user"]:
            user_json = unquote(qs["user"][0])
            obj = json.loads(user_json)
            uid = obj.get("id")
            if uid is not None:
                return str(uid)
    except Exception:
        pass

    # fallback — хэшируем строку стабильно
    return f"user-{abs(hash(init_data))}"


def itunes_search_track(artist: str, title: str) -> Optional[dict]:
    """
    iTunes Search API.
    Возвращает cover + preview_url (30 сек) если найдено.
    """
    q = f"{artist} {title}".strip()
    if not q:
        return None

    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": q,
                "media": "music",
                "entity": "song",
                "limit": 5,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None

        # берём первый результат; при желании можно улучшить матчинг
        item = results[0]
        cover = item.get("artworkUrl100") or item.get("artworkUrl60")
        if cover:
            cover = re.sub(r"/\d+x\d+bb\.jpg", "/600x600bb.jpg", cover)

        preview = item.get("previewUrl")
        return {"cover": cover, "preview_url": preview}
    except Exception:
        return None


# =========================
# 4) APP = FastAPI()
# =========================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# 5) STARTUP
# =========================
@app.on_event("startup")
def startup_event():
    items = load_songs_from_file()
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items
    VOTES.setdefault(CURRENT_WEEK_ID, {})
    USER_VOTES.setdefault(CURRENT_WEEK_ID, {})

    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(items)}", flush=True)


# =========================
# 6) ROUTES
# =========================
class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    is_new: bool = False
    weeks_in_chart: int = 1
    cover: Optional[str] = None
    preview_url: Optional[str] = None
    source: Optional[str] = "manual"


class WeekOut(BaseModel):
    id: int
    title: str
    status: Literal["open", "closed"]


class VoteIn(BaseModel):
    song_ids: List[int] = []


class VoteOut(BaseModel):
    ok: bool
    week_id: int
    user_id: str
    voted_song_ids: List[int]


@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/weeks/current", response_model=WeekOut)
def weeks_current():
    w = get_current_week()
    return WeekOut(**w)


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

    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    if filter == "new":
        items = [s for s in items if bool(s.get("is_new", False))]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in f"{s.get('artist','')} {s.get('title','')}".lower()]

    # ВАЖНО: возвращаем dict-ы; pydantic сам приведёт к SongOut
    return items


@app.post("/weeks/{week_id}/vote", response_model=VoteOut)
def weeks_vote(
    week_id: int,
    payload: VoteIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user_id = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(week_id)

    if CURRENT_WEEK["status"] != "open":
        raise HTTPException(status_code=403, detail="VOTING_CLOSED")

    song_ids = [int(x) for x in (payload.song_ids or [])]

    if len(song_ids) > 10:
        raise HTTPException(status_code=400, detail="TOO_MANY_SONGS_MAX_10")

    existing = {int(s.get("id")) for s in (SONGS_BY_WEEK.get(week_id) or []) if isinstance(s, dict)}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(status_code=400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    USER_VOTES.setdefault(week_id, {})
    VOTES.setdefault(week_id, {})

    # снять прошлые
    prev = USER_VOTES[week_id].get(user_id, [])
    for sid in prev:
        VOTES[week_id][sid] = max(0, VOTES[week_id].get(sid, 0) - 1)

    # поставить новые
    for sid in song_ids:
        VOTES[week_id][sid] = VOTES[week_id].get(sid, 0) + 1

    USER_VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int):
    ensure_week_exists(week_id)
    votes = VOTES.get(week_id, {})
    return [{"song_id": sid, "votes": votes.get(sid, 0)} for sid in sorted(votes.keys())]


@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    try:
        require_admin(x_admin_token)

        week = get_current_week()
        week_id = int(week["id"])
        ensure_week_exists(week_id)

        items = SONGS_BY_WEEK.get(week_id, [])
        if not isinstance(items, list):
            items = []

        updated = 0
        skipped = 0
        processed = 0

        for s in items:
            if not isinstance(s, dict):
                continue

            processed += 1

            cover = s.get("cover")
            preview = s.get("preview_url")

            # пропускаем ТОЛЬКО если уже есть и cover, и preview
            if not force and cover and preview:
                skipped += 1
                continue

            artist = str(s.get("artist") or "").strip()
            title = str(s.get("title") or "").strip()
            if not artist or not title:
                continue

            res = itunes_search_track(artist, title)
            if not res:
                continue

            if (force or not cover) and res.get("cover"):
                s["cover"] = res.get("cover")

            if (force or not preview) and res.get("preview_url"):
                s["preview_url"] = res.get("preview_url")

            updated += 1

        # persist to file (железно) — ПОСЛЕ цикла
        save_songs_to_file(items)

        return {
            "ok": True,
            "week_id": week_id,
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
        }

    except HTTPException:
        raise
    except Exception as e:
        print("❌ ENRICH FAILED")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# Debug endpoints (можно убрать позже)
@app.get("/__debug/songs_path")
def debug_songs_path():
    return {
        "path": str(SONGS_PATH),
        "exists": SONGS_PATH.exists(),
        "size": SONGS_PATH.stat().st_size if SONGS_PATH.exists() else None,
    }


@app.get("/__debug/songs_count")
def debug_songs_count():
    items = SONGS_BY_WEEK.get(CURRENT_WEEK_ID, [])
    return {
        "current_week_id": CURRENT_WEEK_ID,
        "weeks_keys": list(SONGS_BY_WEEK.keys()),
        "count": len(items) if isinstance(items, list) else None,
        "first": items[0] if isinstance(items, list) and len(items) > 0 else None,
    }
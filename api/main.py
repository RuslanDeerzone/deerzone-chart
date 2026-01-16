import os
import json
import time
import hmac
import hashlib
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

import requests
from fastapi import FastAPI, Header, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================
# APP
# =========================
app = FastAPI()


# =========================
# CONFIG
# =========================
CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # должен быть задан в Railway Variables
REQUIRE_INITDATA = os.getenv("REQUIRE_INITDATA", "0") == "1"

BASE_DIR = Path(__file__).resolve().parent  # /app/api
SONGS_PATH = BASE_DIR / "songs.json"
SONGS_BACKUP_PATH = BASE_DIR / "songs.backup.json"

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sincere-perception-production-65ac.up.railway.app",
        "https://web.telegram.org",
        "https://t.me",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# MODELS
# =========================
class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    is_new: bool = False
    weeks_in_chart: int = 1
    source: str = "manual"
    cover: Optional[str] = None
    preview_url: Optional[str] = None
    youtube_url: Optional[str] = None


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


# =========================
# IN-MEMORY STATE
# =========================
SONGS_BY_WEEK: Dict[int, List[Dict[str, Any]]] = {}
VOTES: Dict[int, Dict[int, int]] = {}         # week_id -> {song_id: votes}
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}  # week_id -> {user_id: [song_id,...]}

WEEKS: Dict[int, Dict[str, Any]] = {
    CURRENT_WEEK_ID: {"id": CURRENT_WEEK_ID, "title": f"Week {CURRENT_WEEK_ID}", "status": "open"}
}


# =========================
# HELPERS: ADMIN / AUTH
# =========================
def require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(500, detail="ADMIN_TOKEN is not set on server")
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, detail="BAD_ADMIN_TOKEN")


def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    """
    Упрощённо: для Mini App обычно есть initData.
    В прод-режиме можно включить REQUIRE_INITDATA=1, и тогда без initData голосовать нельзя.
    """
    if not init_data:
        if REQUIRE_INITDATA:
            raise HTTPException(401, detail="NO_TELEGRAM_INITDATA")
        return "dev-user"

    # Пытаемся вытащить user.id из строки initData (без полной криптопроверки, чтобы не ломать запуск)
    # Формат примерно: "query_id=...&user=%7B%22id%22%3A123...%7D&auth_date=...&hash=..."
    try:
        parts = init_data.split("&")
        for p in parts:
            if p.startswith("user="):
                import urllib.parse
                user_json = urllib.parse.unquote(p[len("user="):])
                obj = json.loads(user_json)
                uid = obj.get("id")
                if uid is not None:
                    return str(uid)
    except Exception:
        pass

    # fallback: используем hash как псевдо-id
    try:
        parts = init_data.split("&")
        for p in parts:
            if p.startswith("hash="):
                return "tg-" + p[len("hash="):][:16]
    except Exception:
        pass

    return "tg-user"


# =========================
# HELPERS: SONGS STORAGE (IRON MODE)
# =========================
def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(raw, encoding="utf-8")  # без BOM
    tmp.replace(path)


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"[BOOT] songs.json NOT FOUND: {path}", flush=True)
        return []

    try:
        raw = path.read_text(encoding="utf-8-sig")  # ✅ BOM safe
        data = json.loads(raw)
        if not isinstance(data, list):
            print(f"[BOOT] songs.json is not list, got: {type(data)}", flush=True)
            return []
        data = [x for x in data if isinstance(x, dict)]
        print(f"[BOOT] songs.json loaded: {len(data)} items", flush=True)
        return data
    except Exception:
        print("[BOOT] songs.json FAILED to load:", flush=True)
        print(traceback.format_exc(), flush=True)
        return []


def _normalize_song(s: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["id"] = int(s.get("id", 0) or 0)
    out["artist"] = str(s.get("artist", "")).strip()
    out["title"] = str(s.get("title", "")).strip()
    out["is_new"] = bool(s.get("is_new", False))
    out["weeks_in_chart"] = int(s.get("weeks_in_chart", 1) or 1)
    out["source"] = str(s.get("source", "manual") or "manual")

    out["cover"] = s.get("cover", None)
    out["preview_url"] = s.get("preview_url", None)
    out["youtube_url"] = s.get("youtube_url", None)

    if isinstance(out["cover"], str) and not out["cover"].strip():
        out["cover"] = None
    if isinstance(out["preview_url"], str) and not out["preview_url"].strip():
        out["preview_url"] = None
    if isinstance(out["youtube_url"], str) and not out["youtube_url"].strip():
        out["youtube_url"] = None

    return out


def _normalize_songs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    norm = [_normalize_song(x) for x in items]
    norm = [x for x in norm if x["artist"] and x["title"]]

    # предупреждаем про дубли id
    seen = set()
    dup = []
    for x in norm:
        if x["id"] <= 0:
            dup.append(x)
        if x["id"] in seen:
            dup.append(x)
        seen.add(x["id"])
    if dup:
        print("[BOOT] WARNING: bad/duplicate song ids detected (fix songs.json)", flush=True)

    return norm


def _persist_week_songs(week_id: int, items: List[Dict[str, Any]], *, allow_empty: bool = False) -> None:
    if (not allow_empty) and len(items) == 0:
        raise RuntimeError("Refusing to overwrite songs.json with EMPTY list")

    # backup
    if SONGS_PATH.exists():
        try:
            SONGS_BACKUP_PATH.write_text(SONGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            print("[SAVE] WARNING: failed to write backup", flush=True)

    _atomic_write_json(SONGS_PATH, items)
    SONGS_BY_WEEK[week_id] = items
    print(f"[SAVE] songs.json persisted: week_id={week_id} count={len(items)}", flush=True)


def get_week_songs(week_id: int) -> List[Dict[str, Any]]:
    items = SONGS_BY_WEEK.get(week_id, [])
    return items if isinstance(items, list) else []


# =========================
# HELPERS: WEEK
# =========================
def ensure_week_exists(week_id: int) -> None:
    if week_id not in WEEKS:
        raise HTTPException(404, detail="WEEK_NOT_FOUND")


def get_current_week() -> Dict[str, Any]:
    return WEEKS[CURRENT_WEEK_ID]


# =========================
# iTunes SEARCH
# =========================
def itunes_search_track(artist: str, title: str) -> Optional[Dict[str, str]]:
    """
    Возвращает {"cover": ..., "preview_url": ...} или None
    """
    q = f"{artist} {title}".strip()
    if not q:
        return None

    url = "https://itunes.apple.com/search"
    params = {
        "term": q,
        "media": "music",
        "entity": "song",
        "limit": 1,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        item = results[0]
        cover = item.get("artworkUrl100") or item.get("artworkUrl60") or None
        preview = item.get("previewUrl") or None
        # попробуем чуть больше размер обложки
        if cover and "100x100" in cover:
            cover = cover.replace("100x100", "300x300")
        return {"cover": cover, "preview_url": preview}
    except Exception:
        return None


# =========================
# STARTUP
# =========================
@app.on_event("startup")
def startup_event():
    items = _load_json_list(SONGS_PATH)
    items = _normalize_songs(items)
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items

    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(items)}", flush=True)


# =========================
# BASIC ENDPOINTS
# =========================
@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


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

    ensure_week_exists(week_id)
    items = get_week_songs(week_id)

    if filter == "new":
        items = [s for s in items if bool(s.get("is_new", False))]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in (f"{s.get('artist','')} {s.get('title','')}".lower())]

    return items


@app.post("/weeks/{week_id}/vote", response_model=VoteOut)
def weeks_vote(
    week_id: int,
    payload: VoteIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user_id = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(week_id)

    if get_current_week()["status"] != "open":
        raise HTTPException(403, detail="VOTING_CLOSED")

    # song ids
    song_ids = [int(x) for x in (payload.song_ids or [])]
    existing = {int(s["id"]) for s in get_week_songs(week_id)}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    USER_VOTES.setdefault(week_id, {})
    VOTES.setdefault(week_id, {})

    # если уже голосовал — перезаписываем (снимаем старые)
    prev = USER_VOTES[week_id].get(user_id, [])
    for sid in prev:
        VOTES[week_id][sid] = max(0, VOTES[week_id].get(sid, 0) - 1)

    for sid in song_ids:
        VOTES[week_id][sid] = VOTES[week_id].get(sid, 0) + 1

    USER_VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int):
    ensure_week_exists(week_id)
    votes = VOTES.get(week_id, {})
    return [{"song_id": sid, "votes": votes.get(sid, 0)} for sid in sorted(votes.keys())]


# =========================
# ADMIN ENDPOINTS
# =========================
@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Подтягивает cover/preview_url из iTunes и СРАЗУ сохраняет в songs.json,
    делая backup и не позволяя затереть пустым списком.
    """
    require_admin(x_admin_token)

    week_id = CURRENT_WEEK_ID
    ensure_week_exists(week_id)

    items = get_week_songs(week_id)

    processed = 0
    updated = 0
    skipped = 0
    errors = 0

    for s in items:
        processed += 1
        cover = s.get("cover")
        preview = s.get("preview_url")

        if (not force) and (cover or preview):
            skipped += 1
            continue

        try:
            res = itunes_search_track(s.get("artist", ""), s.get("title", ""))
            if not res:
                continue

            before_cover = s.get("cover")
            before_preview = s.get("preview_url")

            if (not before_cover) and res.get("cover"):
                s["cover"] = res.get("cover")
            if (not before_preview) and res.get("preview_url"):
                s["preview_url"] = res.get("preview_url")

            if s.get("cover") != before_cover or s.get("preview_url") != before_preview:
                updated += 1

        except Exception:
            errors += 1
            print("[ENRICH] error:", s.get("artist"), "-", s.get("title"), flush=True)
            print(traceback.format_exc(), flush=True)

    norm = _normalize_songs(items)
    _persist_week_songs(week_id, norm, allow_empty=False)

    return {
        "ok": True,
        "week_id": week_id,
        "processed": processed,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


# =========================
# DEBUG
# =========================
@app.get("/__debug/songs_path")
def debug_songs_path():
    return {
        "path": str(SONGS_PATH),
        "exists": SONGS_PATH.exists(),
        "size": SONGS_PATH.stat().st_size if SONGS_PATH.exists() else None,
        "backup_exists": SONGS_BACKUP_PATH.exists(),
        "backup_size": SONGS_BACKUP_PATH.stat().st_size if SONGS_BACKUP_PATH.exists() else None,
    }


@app.get("/__debug/songs_count")
def debug_songs_count():
    items = get_week_songs(CURRENT_WEEK_ID)
    return {
        "current_week_id": CURRENT_WEEK_ID,
        "weeks_keys": list(SONGS_BY_WEEK.keys()),
        "count": len(items),
        "first": items[0] if items else None,
    }

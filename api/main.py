import os
import json
import time
import re
import traceback
from pathlib import Path
from typing import List, Optional, Dict, Literal, Any, Tuple

import requests
from fastapi import FastAPI, Header, HTTPException, Body, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================
# Config / Paths
# =========================
BASE_DIR = Path(__file__).resolve().parent  # /app/api
SONGS_PATH = BASE_DIR / "songs.json"
BACKUP_PATH = BASE_DIR / "songs.backup.json"

CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # задай в Railway Variables

# ВАЖНО: храним песни только как list[dict]
SONGS_BY_WEEK: Dict[int, List[Dict[str, Any]]] = {}

# Голоса в памяти (если хочешь персист — скажешь, добавим votes.json)
VOTES: Dict[int, Dict[str, List[int]]] = {}  # week_id -> user_id -> [song_ids]

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
    weeks_in_chart: int = 1
    is_new: bool = False
    source: str = "manual"
    cover: Optional[str] = None
    preview_url: Optional[str] = None


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

# CORS (Telegram webview + твой фронт + localhost)
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

# Иногда Railway/прокси шлёт OPTIONS не туда — мягкий fallback
@app.options("/{path:path}")
def cors_preflight(path: str):
    return Response(status_code=204)


# =========================
# Helpers: robust storage
# =========================
def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_songs_from_file() -> List[Dict[str, Any]]:
    if not SONGS_PATH.exists():
        print(f"[BOOT] songs.json NOT FOUND: {SONGS_PATH}", flush=True)
        return []
    try:
        raw = SONGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            print(f"[BOOT] songs.json is not list, got {type(data)}", flush=True)
            return []
        print(f"[BOOT] songs.json loaded: {len(data)} items", flush=True)
        return data
    except Exception as e:
        print(f"[BOOT] songs.json FAILED to load: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return []


def normalize_songs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Гарантируем:
    - есть id (уникальный int)
    - artist/title строки
    - cover/preview_url ключи есть (может быть None)
    - weeks_in_chart/is_new/source есть
    """
    out: List[Dict[str, Any]] = []
    next_id = 1

    # если в файле уже есть id — продолжим после максимального
    ids = [x.get("id") for x in items if isinstance(x, dict)]
    ids_int = [i for i in ids if isinstance(i, int)]
    if ids_int:
        next_id = max(ids_int) + 1

    for x in items:
        if not isinstance(x, dict):
            continue

        artist = str(x.get("artist", "")).strip()
        title = str(x.get("title", "")).strip()
        if not artist or not title:
            continue

        sid = x.get("id")
        if not isinstance(sid, int):
            sid = next_id
            next_id += 1

        y: Dict[str, Any] = {
            "id": sid,
            "artist": artist,
            "title": title,
            "weeks_in_chart": int(x.get("weeks_in_chart", 1) or 1),
            "is_new": bool(x.get("is_new", False)),
            "source": str(x.get("source", "manual") or "manual"),
            "cover": x.get("cover", None),
            "preview_url": x.get("preview_url", None),
        }
        out.append(y)

    # гарантируем уникальность id
    seen = set()
    uniq = []
    for s in out:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        uniq.append(s)
    return uniq


def persist_current_week_songs() -> None:
    """
    Записывает текущую неделю обратно в api/songs.json.
    Делает backup и атомарную запись.
    """
    items = SONGS_BY_WEEK.get(CURRENT_WEEK_ID, [])
    if not isinstance(items, list):
        items = []

    # backup предыдущего файла (если был)
    if SONGS_PATH.exists():
        try:
            BACKUP_PATH.write_text(SONGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    _atomic_write_json(SONGS_PATH, items)
    print(f"[SAVE] persisted {len(items)} songs to {SONGS_PATH}", flush=True)


def ensure_week_exists(week_id: int) -> None:
    # у тебя сейчас реально только неделя 3 — так и закрепим
    if week_id != CURRENT_WEEK_ID:
        raise HTTPException(status_code=404, detail="week not found")


def get_current_week() -> Dict[str, Any]:
    # Можно потом сделать title/status из env, но пока стабильно
    return {"id": CURRENT_WEEK_ID, "title": f"Week {CURRENT_WEEK_ID}", "status": "open"}


# =========================
# Telegram auth (soft)
# =========================
def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    """
    В mini app initData есть. В dev/PS может быть пусто — разрешаем.
    Для прод-строгости можно запретить пустое.
    """
    if not init_data:
        # dev mode id
        return "dev-user"

    # Минимально: пытаемся достать user.id из initData (query string)
    # initData выглядит как "query_id=...&user=%7B...%7D&auth_date=...&hash=..."
    m = re.search(r"user=([^&]+)", init_data)
    if not m:
        return "tg-unknown"
    try:
        import urllib.parse
        user_json = urllib.parse.unquote(m.group(1))
        user = json.loads(user_json)
        uid = user.get("id")
        return str(uid) if uid is not None else "tg-unknown"
    except Exception:
        return "tg-unknown"


def require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        # чтобы не стрелять себе в ногу: если токен не задан в env — админка отключена
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured")
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")


# =========================
# iTunes enrich
# =========================
def itunes_search_track(artist: str, title: str) -> Optional[Tuple[str, str]]:
    """
    Возвращает (cover_url, preview_url) или None.
    """
    try:
        q = f"{artist} {title}".strip()
        url = "https://itunes.apple.com/search"
        params = {"term": q, "limit": 5, "media": "music"}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None

        # берем первый наиболее релевантный (можно усложнить, но пока просто)
        top = results[0]
        artwork = top.get("artworkUrl100") or top.get("artworkUrl60") or None
        preview = top.get("previewUrl") or None

        # можно повысить размер обложки (100 -> 600)
        if artwork:
            artwork = artwork.replace("100x100bb.jpg", "600x600bb.jpg")
            artwork = artwork.replace("100x100bb.png", "600x600bb.png")

        return (artwork, preview)
    except Exception:
        return None


# =========================
# Startup: load & normalize
# =========================
@app.on_event("startup")
def startup_event():
    items = load_songs_from_file()
    items = normalize_songs(items)
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items

    print(f"[STARTUP] app_id={id(app)} starting...", flush=True)
    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(SONGS_BY_WEEK.get(CURRENT_WEEK_ID, []))}", flush=True)


# =========================
# Routes
# =========================
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/weeks/current", response_model=WeekOut)
def weeks_current(x_telegram_init_data: Optional[str] = Header(default=None)):
    # мягкая авторизация: не ломаем браузер
    _ = user_id_from_telegram_init_data(x_telegram_init_data)
    return get_current_week()


@app.get("/weeks/{week_id}/songs", response_model=List[SongOut])
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    # мягкая авторизация
    _ = user_id_from_telegram_init_data(x_telegram_init_data)

    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    if filter == "new":
        items = [s for s in items if bool(s.get("is_new", False))]

    if search.strip():
        q = search.strip().lower()
        items = [
            s for s in items
            if q in f"{s.get('artist','')} {s.get('title','')}".lower()
        ]

    return items


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int, x_telegram_init_data: Optional[str] = Header(default=None)):
    _ = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(week_id)
    week_votes = VOTES.get(week_id, {})
    # агрегируем по song_id
    counts: Dict[int, int] = {}
    for _, song_ids in week_votes.items():
        for sid in song_ids:
            counts[sid] = counts.get(sid, 0) + 1
    return [{"song_id": sid, "votes": counts[sid]} for sid in sorted(counts.keys())]


@app.post("/weeks/{week_id}/vote", response_model=VoteOut)
def weeks_vote(
    week_id: int,
    payload: VoteIn = Body(...),
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user_id = user_id_from_telegram_init_data(x_telegram_init_data)
    ensure_week_exists(week_id)

    # базовая валидация
    song_ids = payload.song_ids or []
    if not isinstance(song_ids, list):
        raise HTTPException(status_code=400, detail="song_ids must be list")

    # разрешим максимум 10, чтобы не спамили
    song_ids = [int(x) for x in song_ids[:10]]

    # проверим что id существуют
    existing = {s["id"] for s in SONGS_BY_WEEK.get(week_id, []) if isinstance(s, dict) and isinstance(s.get("id"), int)}
    for sid in song_ids:
        if sid not in existing:
            raise HTTPException(status_code=400, detail=f"invalid song_id: {sid}")

    if week_id not in VOTES:
        VOTES[week_id] = {}
    VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)


# ---------- Admin ----------
@app.post("/admin/weeks/current/songs/bulk")
def admin_add_songs(
    songs: List[Dict[str, Any]] = Body(...),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    items = SONGS_BY_WEEK.get(CURRENT_WEEK_ID, [])
    if not isinstance(items, list):
        items = []

    incoming = normalize_songs(songs)
    # добавляем в конец, избегаем дубликатов по (artist,title)
    seen = {(s["artist"].lower(), s["title"].lower()) for s in items}
    for s in incoming:
        key = (s["artist"].lower(), s["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(s)

    SONGS_BY_WEEK[CURRENT_WEEK_ID] = normalize_songs(items)
    persist_current_week_songs()

    return {"ok": True, "week_id": CURRENT_WEEK_ID, "count": len(SONGS_BY_WEEK[CURRENT_WEEK_ID])}


@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    items = SONGS_BY_WEEK.get(CURRENT_WEEK_ID, [])
    if not isinstance(items, list):
        items = []

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
            new_cover, new_preview = res

            if (not cover) and new_cover:
                s["cover"] = new_cover
            if (not preview) and new_preview:
                s["preview_url"] = new_preview

            # считаем обновлением, если хоть что-то появилось
            if (new_cover and not cover) or (new_preview and not preview):
                updated += 1
        except Exception:
            errors += 1

    SONGS_BY_WEEK[CURRENT_WEEK_ID] = normalize_songs(items)
    persist_current_week_songs()

    return {
        "ok": True,
        "week_id": CURRENT_WEEK_ID,
        "processed": processed,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        }


# ===== DEBUG (safe) =====
@app.get("/__debug/songs_path")
def debug_songs_path():
    try:
        import os as _os
        return {
            "cwd": _os.getcwd(),
            "file": str(SONGS_PATH),
            "exists": bool(getattr(SONGS_PATH, "exists", lambda: False)()),
            "size": SONGS_PATH.stat().st_size if SONGS_PATH.exists() else None,
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/__debug/songs_count")
def debug_songs_count():
    try:
        items = SONGS_BY_WEEK.get(CURRENT_WEEK_ID, [])
        return {
            "current_week_id": CURRENT_WEEK_ID,
            "weeks_keys": list(SONGS_BY_WEEK.keys()),
            "count": len(items) if isinstance(items, list) else None,
            "first": items[0] if isinstance(items, list) and len(items) > 0 else None,
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/__debug/songs_file")
def debug_songs_file():
    try:
        import json as _json
        p = SONGS_PATH
        raw = p.read_text(encoding="utf-8") if p.exists() else ""
        head = raw[:200]

        j = None
        top = None
        count = None
        try:
            j = _json.loads(raw) if raw else None
            top = type(j).__name__ if j is not None else None
            count = len(j) if isinstance(j, list) else None
        except Exception as e:
            top = f"json_error: {e}"

        return {
            "path": str(p),
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else None,
            "top_type": top,
            "list_count": count,
            "head": head,
        }
    except Exception as e:
        return {"error": str(e)}


try:
    print(f"[BOOT-CHECK] app_id={id(app)}", flush=True)
    print(
        f"[BOOT-CHECK] weeks={list(SONGS_BY_WEEK.keys())} "
        f"count={len(SONGS_BY_WEEK.get(CURRENT_WEEK_ID, []))}",
        flush=True,
    )
except Exception as _e:
    print(f"[BOOT-CHECK] failed: {_e}", flush=True)

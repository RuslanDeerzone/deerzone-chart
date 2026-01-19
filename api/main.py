# api/main.py
from __future__ import annotations

# =========================
# 1) IMPORTS
# =========================
import os
import re
import json
import hmac
import time
import hashlib
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Tuple

import requests
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# =========================
# 2) CONFIG / CONSTANTS
# =========================
BASE_DIR = Path(__file__).resolve().parent  # api/
SONGS_PATH = BASE_DIR / "songs.json"
VOTES_PATH = BASE_DIR / "votes.json"

CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # желательно задать

VOTE_LIMIT_PER_USER = int(os.getenv("VOTE_LIMIT_PER_USER", "20"))

ITUNES_COUNTRY = os.getenv("ITUNES_COUNTRY", "US")
ITUNES_LIMIT = int(os.getenv("ITUNES_LIMIT", "5"))

# In-memory stores
SONGS_BY_WEEK: Dict[int, List[dict]] = {}
# votes: week_id -> {song_id(int): votes(int)}
VOTES: Dict[int, Dict[int, int]] = {}
# user_votes: week_id -> {user_id(str): [song_id...]}
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}


# =========================
# 3) HELPERS (IRON MADE)
# =========================
def _now_ts() -> int:
    return int(time.time())


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    _atomic_write_text(path, text)


def _read_json_bom_safe(path: Path) -> Any:
    """
    BOM-safe чтение JSON:
    - utf-8-sig снимает BOM
    - пустой файл -> None
    """
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return None
    return json.loads(raw)


def normalize_songs(items: Any) -> List[dict]:
    """
    Нормализует массив песен:
    - гарантирует dict
    - гарантирует поля: id, artist, title, is_new, weeks_in_chart, source, cover, preview_url, lock_media
    - вычисляет is_current (для вкладки Current) если его нет:
      source == "carryover" -> is_current=True
    """
    if not isinstance(items, list):
        return []

    out: List[dict] = []
    seen_ids: set[int] = set()

    for x in items:
        if not isinstance(x, dict):
            continue

        try:
            sid = int(x.get("id"))
        except Exception:
            continue
        if sid <= 0:
            continue
        # дубль id — оставляем первый, остальные игнор (железно)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        artist = str(x.get("artist") or "").strip()
        title = str(x.get("title") or "").strip()

        # itunes enrich может работать без cover/preview -> разрешаем None
        cover = x.get("cover", None)
        preview_url = x.get("preview_url", None)

        # source: "new" | "carryover" | ...
        source = str(x.get("source") or "").strip() or ("new" if bool(x.get("is_new")) else "carryover")

        is_new = bool(x.get("is_new", False))
        weeks_in_chart = x.get("weeks_in_chart", 1)
        try:
            weeks_in_chart = int(weeks_in_chart)
        except Exception:
            weeks_in_chart = 1

        lock_media = bool(x.get("lock_media", False))

        # current = carryover (если поле не задано явно)
        if "is_current" in x:
            is_current = bool(x.get("is_current"))
        else:
            is_current = (source.lower() == "carryover")

        out.append({
            "id": sid,
            "artist": artist,
            "title": title,
            "is_new": is_new,
            "is_current": is_current,
            "weeks_in_chart": weeks_in_chart,
            "source": source,
            "cover": cover,
            "preview_url": preview_url,
            "lock_media": lock_media,
        })

        out = [x for x in out if isinstance(x, dict)]
        if len(out) == 0 and len(data) > 0:
            print("[BOOT] normalize_songs returned 0 items from non-empty input!", flush=True)
            # спасаем хотя бы то, что было
            out = [x for x in data if isinstance(x, dict)]

    return out


def load_songs_from_file() -> List[dict]:
    """
    Надёжная загрузка songs.json:
    - читает BOM-safe (utf-8-sig)
    - принимает либо список [...], либо объект {"items":[...]} / {"songs":[...]} / {"3":[...]}
    - никогда молча не "теряет" данные: логирует тип/ошибку
    """
    if not SONGS_PATH.exists():
        print(f"[BOOT] songs.json NOT FOUND: {SONGS_PATH}", flush=True)
        return []

    try:
        raw = SONGS_PATH.read_text(encoding="utf-8-sig")
    except Exception as e:
        print(f"[BOOT] songs.json READ FAILED: {e}", flush=True)
        return []

    try:
        data = json.loads(raw) if raw.strip() else []
    except Exception as e:
        print(f"[BOOT] songs.json JSON PARSE FAILED: {e}", flush=True)
        # полезно увидеть начало файла в логе
        head = raw[:200].replace("\n", "\\n")
        print(f"[BOOT] songs.json HEAD: {head}", flush=True)
        return []

    # 1) если это dict — пробуем вытащить список песен из популярных контейнеров
    if isinstance(data, dict):
        # варианты контейнеров
        for key in ("items", "songs"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

        # вариант: ключом является номер недели ("3": [...])
        if isinstance(data, dict):
            wk_key = str(CURRENT_WEEK_ID)
            if isinstance(data.get(wk_key), list):
                data = data[wk_key]

    # 2) теперь должен быть list
    if not isinstance(data, list):
        print(f"[BOOT] songs.json INVALID ROOT TYPE: {type(data)} (expected list)", flush=True)
        return []

    # 3) нормализация НЕ должна обнулять всё
    try:
        data = normalize_songs(data)
    except Exception as e:
        print(f"[BOOT] normalize_songs FAILED: {e}", flush=True)
        # в крайнем случае вернём как есть, лишь бы не пропало
        data = [x for x in data if isinstance(x, dict)]

    print(f"[BOOT] songs.json loaded OK: {len(data)} items", flush=True)
    return data

    # 🛡️ предохранитель: если нормализация "обнулила" непустой список — возвращаем сырой список
    if isinstance(data, list) and len(data) == 0 and isinstance(raw_data, list) and len(raw_data) > 0:
        print("[BOOT] normalize_songs wiped songs -> fallback to raw list", flush=True)
        data = raw_data


def save_songs_to_file(items: List[dict]) -> None:
    # сохраняем уже нормализованный список
    _atomic_write_json(SONGS_PATH, normalize_songs(items))


def load_votes_from_file() -> Tuple[Dict[int, Dict[int, int]], Dict[int, Dict[str, List[int]]]]:
    """
    votes.json формат:
    {
      "3": {
        "votes": { "16": 5, "8": 2 },
        "user_votes": { "12345": [16,8] }
      }
    }
    """
    if not VOTES_PATH.exists():
        print(f"[BOOT] votes.json NOT FOUND: {VOTES_PATH}", flush=True)
        return {}, {}

    try:
        data = _read_json_bom_safe(VOTES_PATH)
        if not isinstance(data, dict):
            print(f"[BOOT] votes.json is not dict: {type(data)}", flush=True)
            return {}, {}

        votes_out: Dict[int, Dict[int, int]] = {}
        users_out: Dict[int, Dict[str, List[int]]] = {}

        for wk_str, block in data.items():
            try:
                wk = int(wk_str)
            except Exception:
                continue
            if not isinstance(block, dict):
                continue

            vmap = block.get("votes", {})
            umap = block.get("user_votes", {})

            vv: Dict[int, int] = {}
            if isinstance(vmap, dict):
                for sid_str, cnt in vmap.items():
                    try:
                        sid = int(sid_str)
                        vv[sid] = int(cnt)
                    except Exception:
                        continue

            uu: Dict[str, List[int]] = {}
            if isinstance(umap, dict):
                for uid, ids in umap.items():
                    if not isinstance(uid, str):
                        uid = str(uid)
                    if isinstance(ids, list):
                        clean: List[int] = []
                        for i in ids:
                            try:
                                clean.append(int(i))
                            except Exception:
                                pass
                        uu[uid] = clean

            votes_out[wk] = vv
            users_out[wk] = uu

        print(f"[BOOT] votes.json loaded: weeks={len(votes_out)}", flush=True)
        return votes_out, users_out
    except Exception as e:
        print(f"[BOOT] votes.json FAILED: {e}", flush=True)
        return {}, {}


def save_votes_to_file() -> None:
    data: Dict[str, Any] = {}
    for wk in set(list(VOTES.keys()) + list(USER_VOTES.keys())):
        vmap = VOTES.get(wk, {})
        umap = USER_VOTES.get(wk, {})
        data[str(wk)] = {
            "votes": {str(k): int(v) for k, v in vmap.items()},
            "user_votes": {str(uid): [int(x) for x in xs] for uid, xs in umap.items()},
        }
    _atomic_write_json(VOTES_PATH, data)


def require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured")
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def ensure_week_exists(week_id: int) -> None:
    if week_id != CURRENT_WEEK_ID:
        raise HTTPException(status_code=404, detail="Week not found")


def get_current_week() -> dict:
    return {"id": CURRENT_WEEK_ID}


def _telegram_check_hash(init_data: str, bot_token: str) -> Tuple[bool, Optional[str]]:
    """
    Telegram WebApp initData validation:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    if not init_data or not bot_token:
        return False, None

    try:
        # parse querystring
        pairs = init_data.split("&")
        data: Dict[str, str] = {}
        for p in pairs:
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            data[k] = v

        recv_hash = data.get("hash", "")
        if not recv_hash:
            return False, None

        # data_check_string: sorted key=value excluding hash
        check_items = []
        for k in sorted(data.keys()):
            if k == "hash":
                continue
            check_items.append(f"{k}={data[k]}")
        data_check_string = "\n".join(check_items)

        secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        ok = hmac.compare_digest(calc_hash, recv_hash)

        # user id (если есть user=JSON)
        user_id = None
        u = data.get("user")
        if u:
            try:
                user_obj = json.loads(requests.utils.unquote(u))
                user_id = str(user_obj.get("id"))
            except Exception:
                user_id = None

        return ok, user_id
    except Exception:
        return False, None


def user_id_from_telegram_init_data(init_data: Optional[str]) -> str:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data")

    # если токен не задан — НЕ делаем вид, что всё ок
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is not configured")

    ok, user_id = _telegram_check_hash(init_data, TELEGRAM_BOT_TOKEN)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")
    if not user_id:
        raise HTTPException(status_code=401, detail="Cannot read user id from initData")
    return user_id


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


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
                "limit": ITUNES_LIMIT,
                "country": ITUNES_COUNTRY,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None

        data = r.json()
        results = data.get("results") or []
        if not results:
            return None

        # Лучший матч по artist/title (не просто первый)
        best = None
        best_score = -1

        a0 = _norm(artist)
        t0 = _norm(title)

        for it in results:
            a1 = _norm(it.get("artistName"))
            t1 = _norm(it.get("trackName"))
            score = 0
            if a0 and a0 in a1:
                score += 2
            if t0 and t0 in t1:
                score += 2
            # небольшой бонус за точное совпадение
            if a0 == a1:
                score += 2
            if t0 == t1:
                score += 3
            if score > best_score:
                best_score = score
                best = it

        item = best or results[0]

        cover = item.get("artworkUrl100") or item.get("artworkUrl60")
        if cover:
            cover = re.sub(r"/\d+x\d+bb\.jpg", "/600x600bb.jpg", cover)

        preview = item.get("previewUrl")
        return {"cover": cover, "preview_url": preview}
    except Exception:
        return None


# =========================
# 4) APP
# =========================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# 5) STARTUP
# =========================
@app.on_event("startup")
def startup_event():
    # --- songs ---
    items = load_songs_from_file()
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items if isinstance(items, list) else []

    # --- votes ---
    votes_loaded, users_loaded = load_votes_from_file()
    VOTES.clear()
    USER_VOTES.clear()
    VOTES.update(votes_loaded)
    USER_VOTES.update(users_loaded)

    VOTES.setdefault(CURRENT_WEEK_ID, {})
    USER_VOTES.setdefault(CURRENT_WEEK_ID, {})

    try:
        sz = SONGS_PATH.stat().st_size if SONGS_PATH.exists() else None
    except Exception:
        sz = None

    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_FILE_SIZE={sz}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(SONGS_BY_WEEK.get(CURRENT_WEEK_ID, []))}", flush=True)


# =========================
# 6) MODELS
# =========================
class SongOut(BaseModel):
    id: int
    artist: str
    title: str
    is_new: bool = False
    is_current: bool = False
    weeks_in_chart: int = 1
    source: str = ""
    cover: Optional[str] = None
    preview_url: Optional[str] = None
    lock_media: bool = False


class VoteIn(BaseModel):
    song_ids: List[int] = Field(default_factory=list)


# =========================
# 7) ROUTES
# =========================
@app.get("/weeks/current")
def weeks_current():
    return get_current_week()


@app.get("/weeks/{week_id}/songs", response_model=List[SongOut])
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new", "current"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    # auth (в Mini App initData есть; для браузера допускаем пустое)
    try:
        if x_telegram_init_data:
            _ = user_id_from_telegram_init_data(x_telegram_init_data)
    except Exception:
        pass

    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    # фильтры
    if filter == "new":
        items = [s for s in items if bool((s or {}).get("is_new", False))]
    elif filter == "current":
        items = [s for s in items if bool((s or {}).get("is_current", False))]

    # поиск
    q = _norm(search)
    if q:
        items = [
            s for s in items
            if q in _norm((s or {}).get("artist")) or q in _norm((s or {}).get("title"))
        ]

    # сортировка: artist A-Z, затем title A-Z
    items = items[:]
    items.sort(key=lambda s: (_norm((s or {}).get("artist")), _norm((s or {}).get("title"))))

    return items


@app.post("/weeks/{week_id}/vote")
def vote_week(
    week_id: int,
    body: VoteIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    ensure_week_exists(week_id)

    # строго требуем Telegram initData
    user_id = user_id_from_telegram_init_data(x_telegram_init_data)

    song_ids = [int(x) for x in (body.song_ids or []) if int(x) > 0]
    if not song_ids:
        raise HTTPException(status_code=400, detail="song_ids is empty")

    # лимит
    if len(song_ids) > VOTE_LIMIT_PER_USER:
        raise HTTPException(status_code=400, detail=f"Too many votes. Limit={VOTE_LIMIT_PER_USER}")

    # проверка существования песен
    items = SONGS_BY_WEEK.get(week_id, [])
    exists = {int(s.get("id")) for s in items if isinstance(s, dict) and s.get("id") is not None}
    for sid in song_ids:
        if sid not in exists:
            raise HTTPException(status_code=400, detail=f"Unknown song id: {sid}")

    # повторное голосование
    USER_VOTES.setdefault(week_id, {})
    if user_id in USER_VOTES[week_id] and USER_VOTES[week_id][user_id]:
        raise HTTPException(status_code=409, detail="User already voted this week")

    # записываем
    VOTES.setdefault(week_id, {})
    for sid in song_ids:
        VOTES[week_id][sid] = int(VOTES[week_id].get(sid, 0)) + 1

    USER_VOTES[week_id][user_id] = song_ids

    # persist
    save_votes_to_file()

    return {"ok": True, "week_id": week_id, "user_id": user_id, "votes": len(song_ids)}


@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    ВАЖНО: Body должен быть ЛИБО "false"/"true" (как boolean),
    ЛИБО просто false/true, но не {"force": true}.
    """
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

            # 🔒 ручная фиксация — НЕ трогаем
            if s.get("lock_media") is True:
                skipped += 1
                continue

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

        # и обновим память нормализованно (чтобы is_current подсчитал и т.д.)
        SONGS_BY_WEEK[week_id] = load_songs_from_file()

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


@app.get("/admin/weeks/{week_id}/votes/summary")
def admin_votes_summary(
    week_id: int,
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)
    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, [])
    if not isinstance(items, list):
        items = []

    votes_map = VOTES.get(week_id, {})
    if not isinstance(votes_map, dict):
        votes_map = {}

    rows: List[Dict[str, Any]] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        sid = int(s.get("id") or 0)
        rows.append({
            "id": sid,
            "artist": s.get("artist"),
            "title": s.get("title"),
            "is_new": bool(s.get("is_new", False)),
            "is_current": bool(s.get("is_current", False)),
            "weeks_in_chart": s.get("weeks_in_chart"),
            "source": s.get("source"),
            "cover": s.get("cover"),
            "preview_url": s.get("preview_url"),
            "lock_media": bool(s.get("lock_media", False)),
            "votes": int(votes_map.get(sid, 0)),
        })

    rows.sort(key=lambda r: (-int(r.get("votes", 0)), _norm(r.get("artist")), _norm(r.get("title"))))

    return {"ok": True, "week_id": week_id, "total_songs": len(rows), "rows": rows}


@app.get("/admin/weeks/{week_id}/votes/top")
def admin_votes_top(
    week_id: int,
    n: int = 10,
    x_admin_token: Optional[str] = Header(default=None),
):
    data = admin_votes_summary(week_id, x_admin_token)
    n = max(0, int(n))
    return {
        "ok": True,
        "week_id": data["week_id"],
        "total_songs": data["total_songs"],
        "n": n,
        "rows": data["rows"][:n],
    }


@app.get("/admin/weeks/current/votes/summary")
def admin_votes_summary_current(
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)
    wk = get_current_week()
    return admin_votes_summary(int(wk["id"]), x_admin_token)


# -------------------------
# Debug endpoints
# -------------------------
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


@app.get("/__debug/songs_parse")
def debug_songs_parse():
    """
    ЖЕЛЕЗНЫЙ дебаг: покажет, что реально лежит в songs.json и почему не грузится.
    """
    try:
        if not SONGS_PATH.exists():
            return {"path": str(SONGS_PATH), "exists": False}

        raw = SONGS_PATH.read_text(encoding="utf-8-sig")
        head = raw[:250]

        try:
            data = json.loads(raw) if raw.strip() else None
            top_type = type(data).__name__
            list_count = len(data) if isinstance(data, list) else None
        except Exception as e:
            top_type = f"json_error: {e}"
            list_count = None

        return {
            "path": str(SONGS_PATH),
            "exists": True,
            "size": SONGS_PATH.stat().st_size,
            "top_type": top_type,
            "list_count": list_count,
            "head": head,
        }
    except Exception as e:
        return {"error": str(e)}

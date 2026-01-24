# api/main.py
from __future__ import annotations

# =========================
# 1) IMPORTS
# =========================
import os
import shutil
import re
import json
import hmac
import time
import hashlib
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Tuple
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl
from difflib import SequenceMatcher

import requests
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


def _pick_persistent_path(primary, fallback):
    """
    Берём primary (обычно /data/...), если файл/директория доступны.
    Иначе используем fallback (например, /app/api/...), чтобы не падать.
    """
    try:
        p = Path(primary)
        # если это файл — проверим, что родитель существует/можно создать
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return Path(fallback)

class SongsReplaceIn(BaseModel):
    items: List[dict]


# =========================
# 2) CONFIG / CONSTANTS
# =========================

BASE_DIR = Path(__file__).resolve().parent  # /app/api

# songs.json ОСТАЁТСЯ В API — НЕ В VOLUME
SONGS_PATH = BASE_DIR / "songs.json"

# всё, что должно переживать деплой — в volume (/data)
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
VOTES_PATH = DATA_DIR / "votes.json"
WEEK_META_PATH = DATA_DIR / "week_meta.json"
ARCHIVE_DIR = DATA_DIR / "archive"

VOTES_PATH = _pick_persistent_path(VOTES_PATH, BASE_DIR / "votes.json")
WEEK_META_PATH = _pick_persistent_path(WEEK_META_PATH, BASE_DIR / "week_meta.json")
ARCHIVE_DIR = (_pick_persistent_path(ARCHIVE_DIR / ".keep", BASE_DIR / "archive" / ".keep")).parent

def _ensure_data_dir() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[BOOT] DATA_DIR mkdir failed: {DATA_DIR} -> {e}", flush=True)


def _seed_file_if_missing(dst: Path, src: Path) -> None:
    """
    Если dst отсутствует, но src существует — копируем.
    НИЧЕГО не трогаем, если dst уже есть (чтобы не затирать volume).
    """
    try:
        if dst.exists():
            return
        if not src.exists():
            print(f"[BOOT] SEED source missing: {src}", flush=True)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"[BOOT] SEEDED {dst} <- {src}", flush=True)
    except Exception as e:
        print(f"[BOOT] SEED failed {dst} <- {src}: {e}", flush=True)

CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # желательно задать

# лимит песен в одном голосовании (сколько треков можно выбрать за раз)
VOTE_LIMIT_PER_USER = int(os.getenv("VOTE_LIMIT_PER_USER", "20"))

ITUNES_COUNTRY = os.getenv("ITUNES_COUNTRY", "US")
ITUNES_LIMIT = int(os.getenv("ITUNES_LIMIT", "5"))

MSK = ZoneInfo("Europe/Moscow")

VOTING_CLOSE_WEEKDAY = 5  # Saturday (Mon=0 ... Sun=6)
VOTING_CLOSE_HOUR = 18
VOTING_CLOSE_MINUTE = 0

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


def _atomic_write_json(path: Path, obj) -> None:
    """
    Пишем JSON атомарно: сначала во временный файл рядом, потом replace.
    ВАЖНО: path уже абсолютный/полный, НЕ надо добавлять API_DIR повторно.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")

    data = json.dumps(obj, ensure_ascii=False, indent=2)

    # Пишем без BOM (utf-8)
    tmp_path.write_text(data, encoding="utf-8")

    # атомарная замена
    tmp_path.replace(path)


def _ensure_dir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[BOOT] cannot mkdir {p}: {e}", flush=True)

def _path_is_writable(p: Path) -> bool:
    try:
        _ensure_dir(p.parent)
        test = p.parent / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


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
    - пропускает всё, что не dict
    - id обязателен и > 0
    - не допускает дубли id (оставляет первый)
    - аккуратно заполняет поля по умолчанию
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

        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        artist = str(x.get("artist") or "").strip()
        title = str(x.get("title") or "").strip()

        cover = x.get("cover", None)
        preview_url = x.get("preview_url", None)

        source = str(x.get("source") or "").strip()
        if not source:
            source = "new" if bool(x.get("is_new")) else "carryover"

        is_new = bool(x.get("is_new", False))

        weeks_in_chart = x.get("weeks_in_chart", 1)
        try:
            weeks_in_chart = int(weeks_in_chart)
        except Exception:
            weeks_in_chart = 1

        lock_media = bool(x.get("lock_media", False))

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

    return out


def load_songs_from_file() -> List[dict]:
    """
    Надёжная загрузка songs.json:
    - читает BOM-safe (utf-8-sig)
    - принимает либо список [...], либо объект {"items":[...]} / {"songs":[...]} / {"3":[...]}
    - НЕ теряет данные из-за нормализации
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
        loaded = json.loads(raw) if raw.strip() else []
    except Exception as e:
        print(f"[BOOT] songs.json JSON PARSE FAILED: {e}", flush=True)
        head = raw[:250].replace("\n", "\\n")
        print(f"[BOOT] songs.json HEAD: {head}", flush=True)
        return []

    data = loaded

    # если root dict — пробуем контейнеры
    if isinstance(data, dict):
        for key in ("items", "songs"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

        # вариант: ключом является номер недели ("3": [...])
        if isinstance(data, dict):
            wk_key = str(CURRENT_WEEK_ID)
            if isinstance(data.get(wk_key), list):
                data = data[wk_key]

    if not isinstance(data, list):
        print(f"[BOOT] songs.json INVALID ROOT TYPE: {type(data)} (expected list)", flush=True)
        return []

    # приводим к списку dict
    raw_data = data
    data = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    try:
        data = normalize_songs(data)
    except Exception as e:
        print(f"[BOOT] normalize_songs FAILED: {e}", flush=True)
        data = [x for x in raw_data if isinstance(x, dict)] if isinstance(raw_data, list) else []

    # 🛡️ предохранитель: если нормализация "обнулила" непустой список — возвращаем сырой список
    if isinstance(data, list) and len(data) == 0 and isinstance(raw_data, list) and len(raw_data) > 0:
        print("[BOOT] normalize_songs wiped songs -> fallback to raw list", flush=True)
        data = [x for x in raw_data if isinstance(x, dict)]

    print(f"[BOOT] songs.json loaded OK: {len(data)} items", flush=True)
    return data


def save_songs_to_file(items: List[dict]) -> None:
    # нормализуем
    norm = normalize_songs(items)

    # 🛡️ если нормализация неожиданно "обнулила" непустой список — НЕ ПИШЕМ []
    # сохраняем хотя бы сырые dict-объекты, чтобы не потерять файл
    if len(norm) == 0:
        raw_list = [x for x in (items or []) if isinstance(x, dict)]
        if len(raw_list) > 0:
            print("[WARN] normalize_songs returned 0 -> writing raw_list to avoid wiping songs.json", flush=True)
            _atomic_write_json(SONGS_PATH, raw_list)
            return

    _atomic_write_json(SONGS_PATH, norm)


def load_votes_from_file() -> Tuple[Dict[int, Dict[int, int]], Dict[int, Dict[str, List[int]]]]:
    """
    Читает votes.json и возвращает (VOTES, USER_VOTES).
    Поддерживает:
    - новый формат: { "3": {"votes": {...}, "user_votes": {...}}, ... }
    - старый формат (если вдруг был): { "3": {...} }
    """
    if not VOTES_PATH.exists():
        print(f"[BOOT] votes.json NOT FOUND: {VOTES_PATH}", flush=True)
        return {}, {}

    try:
        raw = VOTES_PATH.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            print(f"[BOOT] votes.json is not dict, got {type(data)}", flush=True)
            return {}, {}

        votes_out: Dict[int, Dict[int, int]] = {}
        user_out: Dict[int, Dict[str, List[int]]] = {}

        for wk_str, payload in data.items():
            try:
                wk = int(wk_str)
            except Exception:
                continue

            # ---- НОВЫЙ ФОРМАТ ----
            if isinstance(payload, dict) and ("votes" in payload or "user_votes" in payload):
                v = payload.get("votes", {})
                u = payload.get("user_votes", {})

                vmap: Dict[int, int] = {}
                if isinstance(v, dict):
                    for sid_str, cnt in v.items():
                        try:
                            vmap[int(sid_str)] = int(cnt)
                        except Exception:
                            continue

                umap: Dict[str, List[int]] = {}
                if isinstance(u, dict):
                    for uid, arr in u.items():
                        if not isinstance(arr, list):
                            continue
                        out_ids: List[int] = []
                        for x in arr:
                            try:
                                out_ids.append(int(x))
                            except Exception:
                                continue
                        umap[str(uid)] = out_ids

                votes_out[wk] = vmap
                user_out[wk] = umap
                continue

            # ---- СТАРЫЙ ФОРМАТ (на всякий) ----
            if isinstance(payload, dict):
                # если вдруг там лежит просто мапа песня->голоса
                vmap: Dict[int, int] = {}
                for sid_str, cnt in payload.items():
                    try:
                        vmap[int(sid_str)] = int(cnt)
                    except Exception:
                        continue
                votes_out[wk] = vmap
                user_out.setdefault(wk, {})

        print(f"[BOOT] votes.json loaded: weeks={len(votes_out)}", flush=True)
        return votes_out, user_out

    except Exception as e:
        print(f"[BOOT] votes.json FAILED to load: {e}", flush=True)
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


def _telegram_check_hash(init_data: str, bot_token: str) -> tuple[bool, str | None, dict]:
    if not init_data or not isinstance(init_data, str):
        return False, "EMPTY_INIT_DATA", {}
    if not bot_token:
        return False, "TELEGRAM_BOT_TOKEN_EMPTY", {}

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return False, "BAD_INIT_DATA_FORMAT", {}

    data = dict(pairs)
    received_hash = data.get("hash")
    if not received_hash:
        return False, "NO_HASH", data

    check_pairs = [(k, v) for (k, v) in data.items() if k != "hash"]
    check_pairs.sort(key=lambda kv: kv[0])
    data_check_string = "\n".join([f"{k}={v}" for k, v in check_pairs])

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, (received_hash or "").lower()):
        return False, "HASH_MISMATCH", {"keys": sorted(list(data.keys()))}

    return True, None, data


def user_id_from_telegram_init_data(init_data: str | None) -> str:
    ok, err, data = _telegram_check_hash(init_data or "", TELEGRAM_BOT_TOKEN)
    if not ok:
        raise HTTPException(status_code=401, detail=f"TELEGRAM_AUTH_FAILED:{err}")

    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="TELEGRAM_NO_USER")

    try:
        u = json.loads(user_raw)
        uid = u.get("id")
        if not uid:
            raise ValueError("no id")
        return str(uid)
    except Exception:
        raise HTTPException(status_code=401, detail="TELEGRAM_BAD_USER_JSON")


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\(.*?\)", " ", s)          # убираем скобки (feat., ver.)
    s = s.replace("feat.", " ").replace("ft.", " ")
    s = re.sub(r"[^a-z0-9가-힣]+", " ", s)    # пунктуация -> пробел
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _score(artist_in: str, title_in: str, artist_it: str, title_it: str) -> float:
    a1, t1 = _norm(artist_in), _norm(title_in)
    a2, t2 = _norm(artist_it), _norm(title_it)
    a = SequenceMatcher(None, a1, a2).ratio()
    t = SequenceMatcher(None, t1, t2).ratio()
    return 0.45 * a + 0.55 * t

def itunes_search_track(artist: str, title: str) -> Optional[dict]:
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

        best = None
        best_score = -1.0
        for item in results:
            sc = _score(artist, title, item.get("artistName",""), item.get("trackName",""))
            if sc > best_score:
                best_score = sc
                best = item

        # порог — ниже него лучше НЕ трогать, чем поставить чужое
        if not best or best_score < 0.78:
            return None

        cover = best.get("artworkUrl100") or best.get("artworkUrl60")
        if cover:
            cover = re.sub(r"/\d+x\d+bb\.(jpg|webp)$", "/600x600bb.\\1", cover)

        preview = best.get("previewUrl")
        return {"cover": cover, "preview_url": preview}
    except Exception:
        return None


def _read_week_meta() -> dict:
    try:
        if not WEEK_META_PATH.exists():
            return {}
        raw = WEEK_META_PATH.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

# =========================
# WEEK META (voting window)
# =========================

def load_week_meta() -> dict:
    """
    week_meta.json:
    {
      "weeks": {
        "3": {
          "opened_at": "2026-01-20T12:00:00+03:00",
          "voting_closes_at": "2026-01-24T15:00:00Z"
        }
      }
    }
    """
    if not WEEK_META_PATH.exists():
        return {"weeks": {}}

    try:
        raw = WEEK_META_PATH.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return {"weeks": {}}
        weeks = data.get("weeks")
        if not isinstance(weeks, dict):
            weeks = {}
        return {"weeks": weeks}
    except Exception as e:
        print(f"[BOOT] week_meta.json FAILED: {e}", flush=True)
        return {"weeks": {}}

def save_week_meta(meta: dict) -> None:
    _atomic_write_json(WEEK_META_PATH, meta)

def get_next_song_id(meta: dict) -> int:
    try:
        return int(meta.get("next_song_id") or 1)
    except Exception:
        return 1

def set_next_song_id(meta: dict, next_id: int) -> None:
    meta["next_song_id"] = int(next_id)

def ensure_next_song_id(meta: dict, items: list[dict]) -> dict:
    if meta.get("next_song_id"):
        return meta
    mx = 0
    for s in items:
        try:
            mx = max(mx, int(s.get("id") or 0))
        except Exception:
            pass
    meta["next_song_id"] = mx + 1
    save_week_meta(meta)
    return meta

def next_saturday_18_msk_iso(now_utc: Optional[datetime] = None) -> str:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    now_msk = now_utc.astimezone(MSK)
    days_ahead = (VOTING_CLOSE_WEEKDAY - now_msk.weekday()) % 7

    target = now_msk.replace(
        hour=VOTING_CLOSE_HOUR,
        minute=VOTING_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    ) + timedelta(days=days_ahead)

    if target <= now_msk:
        target += timedelta(days=7)

    target_utc = target.astimezone(timezone.utc)
    return target_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _get_week_block(meta: dict, week_id: int) -> dict:
    weeks = meta.get("weeks") if isinstance(meta, dict) else None
    if not isinstance(weeks, dict):
        return {}
    wk = weeks.get(str(int(week_id)))
    return wk if isinstance(wk, dict) else {}

def get_week_opened_at_dt(meta: dict, week_id: int) -> Optional[datetime]:
    wk = _get_week_block(meta, week_id)
    s = wk.get("opened_at")
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK)
    except Exception:
        return None

def get_week_voting_closes_dt_utc(meta: dict, week_id: int) -> datetime:
    wk = _get_week_block(meta, week_id)
    closes_at = wk.get("voting_closes_at")
    if not isinstance(closes_at, str) or not closes_at.strip():
        closes_at = next_saturday_18_msk_iso()

    try:
        return datetime.fromisoformat(closes_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        # если руками сломали формат — не блокируем навсегда
        return datetime.now(timezone.utc) + timedelta(days=3650)

def assert_voting_open(meta: dict, week_id: int) -> None:
    """
    1) Голосование разрешено только если неделя "открыта" (есть opened_at).
    2) И только до voting_closes_at (суббота 18:00 МСК -> в UTC).
    """
    opened_dt = get_week_opened_at_dt(meta, week_id)
    if opened_dt is None:
        raise HTTPException(status_code=403, detail="VOTING_NOT_OPENED_YET")

    closes_dt_utc = get_week_voting_closes_dt_utc(meta, week_id)
    if datetime.now(timezone.utc) >= closes_dt_utc:
        raise HTTPException(status_code=403, detail="VOTING_CLOSED")

def mark_week_opened(week_id: int) -> dict:
    meta = load_week_meta()
    weeks = meta.get("weeks")
    if not isinstance(weeks, dict):
        weeks = {}
        meta["weeks"] = weeks

    wk_key = str(int(week_id))
    weeks.setdefault(wk_key, {})
    weeks[wk_key]["opened_at"] = datetime.now(MSK).replace(microsecond=0).isoformat()
    weeks[wk_key]["voting_closes_at"] = next_saturday_18_msk_iso()

    save_week_meta(meta)
    return meta



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
    global CURRENT_WEEK_ID

    _ensure_dir(VOTES_PATH.parent)
    _ensure_dir(WEEK_META_PATH.parent)
    _ensure_dir(ARCHIVE_DIR)

    _ensure_data_dir()

    meta = load_week_meta()
    try:
        CURRENT_WEEK_ID = int(meta.get("current_week_id") or CURRENT_WEEK_ID)
    except Exception:
        pass

    items = load_songs_from_file()
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items if isinstance(items, list) else []

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


class NewTrackIn(BaseModel):
    artist: str
    title: str
    source: Optional[str] = "new"
    lock_media: Optional[bool] = False  # если трек ещё не в iTunes — ставим True

class RolloverIn(BaseModel):
    new_tracks: List[NewTrackIn]
    top_n: int = 20
    max_weeks_in_chart: int = 10


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
    try:
        ensure_week_exists(week_id)

        meta = load_week_meta()
        assert_voting_open(meta, week_id)

        # строго требуем Telegram initData
        user_id = user_id_from_telegram_init_data(x_telegram_init_data)

        song_ids = [int(x) for x in (body.song_ids or []) if int(x) > 0]
        if not song_ids:
            raise HTTPException(status_code=400, detail="song_ids is empty")

        # лимит (20)
        if len(song_ids) > VOTE_LIMIT_PER_USER:
            raise HTTPException(status_code=400, detail=f"Too many votes. Limit={VOTE_LIMIT_PER_USER}")

        # проверка существования песен
        items = SONGS_BY_WEEK.get(week_id, [])
        if not isinstance(items, list):
            items = []
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

        save_votes_to_file()

        return {"ok": True, "week_id": week_id, "user_id": user_id, "votes": len(song_ids)}

    except HTTPException:
        raise
    except Exception as e:
        print("❌ VOTE CRASH", flush=True)
        print(traceback.format_exc(), flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/weeks/{week_id}/archive")
def admin_archive_week(
    week_id: int,
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)
    ensure_week_exists(week_id)

    items = SONGS_BY_WEEK.get(week_id, []) or []
    vmap = VOTES.get(week_id, {}) or {}
    umap = USER_VOTES.get(week_id, {}) or {}

    payload = {
        "week_id": week_id,
        "archived_at": datetime.now(MSK).replace(microsecond=0).isoformat(),
        "unique_voters": len([u for u in umap.keys()]),
        "songs": items,
        "votes": {str(k): int(v) for k, v in vmap.items()},
    }

    out = ARCHIVE_DIR / f"week_{week_id}.json"
    _atomic_write_json(out, payload)

    return {"ok": True, "file": str(out), "week_id": week_id, "unique_voters": payload["unique_voters"]}


class AggregateIn(BaseModel):
    weeks: List[int] = Field(default_factory=list)

@app.post("/admin/votes/aggregate")
def admin_aggregate_votes(
    body: AggregateIn,
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    weeks = [int(x) for x in (body.weeks or []) if int(x) > 0]
    if not weeks:
        raise HTTPException(status_code=400, detail="weeks is empty")

    total_votes: Dict[int, int] = {}
    song_meta: Dict[int, dict] = {}
    total_unique_voters = 0

    for wk in weeks:
        p = ARCHIVE_DIR / f"week_{wk}.json"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"archive missing for week {wk}")

        data = _read_json_bom_safe(p)
        votes = data.get("votes", {}) if isinstance(data, dict) else {}
        songs = data.get("songs", []) if isinstance(data, dict) else []
        total_unique_voters += int(data.get("unique_voters") or 0)

        # сохраняем мету песен
        if isinstance(songs, list):
            for s in songs:
                if isinstance(s, dict) and s.get("id") is not None:
                    sid = int(s["id"])
                    song_meta.setdefault(sid, {"id": sid, "artist": s.get("artist",""), "title": s.get("title","")})

        if isinstance(votes, dict):
            for sid_str, cnt in votes.items():
                try:
                    sid = int(sid_str)
                    total_votes[sid] = int(total_votes.get(sid, 0)) + int(cnt)
                except Exception:
                    continue

    # соберём таблицу
    rows = []
    for sid, cnt in total_votes.items():
        m = song_meta.get(sid, {"id": sid, "artist": "", "title": ""})
        rows.append({**m, "votes": cnt})

    rows.sort(key=lambda r: int(r.get("votes", 0)), reverse=True)

    return {
        "ok": True,
        "weeks": weeks,
        "unique_voters_sum": total_unique_voters,
        "rows": rows,
    }


@app.post("/admin/weeks/current/voting/open")
def admin_open_voting_current_week(
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)

    week_id = CURRENT_WEEK_ID
    meta = mark_week_opened(week_id)  # ДОЛЖНО записать в WEEK_META_PATH (то есть /data/week_meta.json)
    return {"ok": True, "week_id": week_id, "meta": meta}


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

            # ✅ если НЕ force — пропускаем только когда уже всё заполнено
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
        mark_week_opened(week_id)

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


@app.post("/admin/weeks/{week_id}/songs/replace")
def admin_replace_songs(
    week_id: int,
    body: SongsReplaceIn,
    x_admin_token: Optional[str] = Header(default=None),
):

    require_admin(x_admin_token)
    ensure_week_exists(week_id)

    if not isinstance(body.items, list):
        raise HTTPException(status_code=400, detail="BAD_ITEMS")

    norm = normalize_songs(body.items)

    # 🛡️ если прислали непусто, но после нормализации стало пусто — значит payload битый
    if len(norm) == 0 and len(body.items) > 0:
        raise HTTPException(status_code=400, detail="BAD_ITEMS_NORMALIZE_WIPED")

    SONGS_BY_WEEK[week_id] = norm
    save_songs_to_file(norm)

    return {"ok": True, "week_id": week_id, "count": len(norm)}


@app.get("/__debug/votes_path")
def debug_votes_path():
    try:
        exists = VOTES_PATH.exists()
        size = VOTES_PATH.stat().st_size if exists else None
    except Exception:
        exists, size = False, None
    return {"path": str(VOTES_PATH), "exists": exists, "size": size}

@app.get("/__debug/votes_loaded")
def debug_votes_loaded():
    # покажет какие недели реально в памяти
    weeks = sorted(list(VOTES.keys()))
    return {
        "weeks": weeks,
        "current_week_id": CURRENT_WEEK_ID,
        "votes_week_keys": sorted(list(VOTES.get(CURRENT_WEEK_ID, {}).keys()))[:10],
        "users_count": len(USER_VOTES.get(CURRENT_WEEK_ID, {})),
    }


@app.post("/admin/weeks/{week_id}/rollover")
def admin_rollover_week(
    week_id: int,
    body: RolloverIn,
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(x_admin_token)
    ensure_week_exists(week_id)

    prev_week_id = week_id - 1
    if prev_week_id <= 0:
        raise HTTPException(status_code=400, detail="NO_PREVIOUS_WEEK")

    # прошлые песни и голоса
    prev_items = SONGS_BY_WEEK.get(prev_week_id, [])
    if not isinstance(prev_items, list):
        prev_items = []

    prev_votes = VOTES.get(prev_week_id, {})
    if not isinstance(prev_votes, dict):
        prev_votes = {}

    # сортируем песни прошлой недели по голосам (desc)
    def votes_of(song: dict) -> int:
        try:
            sid = int(song.get("id") or 0)
            return int(prev_votes.get(sid, 0))
        except Exception:
            return 0

    # только dict
    prev_items = [s for s in prev_items if isinstance(s, dict) and s.get("id") is not None]

    prev_items_sorted = sorted(
        prev_items,
        key=lambda s: (-votes_of(s), str(s.get("artist") or "").lower(), str(s.get("title") or "").lower())
    )

    top_n = max(0, int(body.top_n or 20))
    max_weeks = max(1, int(body.max_weeks_in_chart or 10))

    carried: list[dict] = []
    for s in prev_items_sorted[:top_n]:
        # если трек уже 10 недель — вылетает
        w = int(s.get("weeks_in_chart") or 1)
        if w >= max_weeks:
            continue

        ns = dict(s)  # копия
        ns["is_new"] = False
        ns["source"] = "current"
        ns["weeks_in_chart"] = w + 1
        carried.append(ns)

    # meta: current_week_id + next_song_id
    meta = load_week_meta()
    meta = ensure_next_song_id(meta, prev_items)

    next_id = get_next_song_id(meta)

    # добавляем новинки
    new_items: list[dict] = []
    for t in (body.new_tracks or []):
        artist = str(t.artist or "").strip()
        title = str(t.title or "").strip()
        if not artist or not title:
            continue

        new_items.append({
            "id": next_id,
            "artist": artist,
            "title": title,
            "is_new": True,
            "weeks_in_chart": 1,
            "cover": None,
            "preview_url": None,
            "source": t.source or "new",
            "lock_media": bool(t.lock_media or False),
        })
        next_id += 1

    # итоговый состав недели
    items = carried + new_items

    # применяем
    SONGS_BY_WEEK[week_id] = items
    save_songs_to_file(items)

    # подготовим структуры голосов на новую неделю
    VOTES.setdefault(week_id, {})
    USER_VOTES.setdefault(week_id, {})

    # обновим meta: текущая неделя + следующий id
    meta["current_week_id"] = int(week_id)
    set_next_song_id(meta, next_id)
    save_week_meta(meta)

    # открываем голосование (ты уже это делал ранее через mark_week_opened)
    try:
        mark_week_opened(week_id)
    except Exception:
        pass

    return {
        "ok": True,
        "week_id": week_id,
        "prev_week_id": prev_week_id,
        "carried": len(carried),
        "new_added": len(new_items),
        "total": len(items),
        "next_song_id": next_id,
    }


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


@app.get("/__debug/songs_file")
def debug_songs_file():
    p = SONGS_PATH
    if not p.exists():
        return {"path": str(p), "exists": False}

    # читаем байты, чтобы увидеть BOM и не упереться в декодирование
    b = p.read_bytes()
    bom = b.startswith(b"\xef\xbb\xbf")

    head_bytes = b[:400]  # сырой хед (на всякий)
    try:
        head_text = head_bytes.decode("utf-8", errors="replace")
    except Exception:
        head_text = None

    # пробуем парсить корректно (BOM-safe)
    top_type = None
    list_count = None
    err = None
    try:
        text = b.decode("utf-8-sig")
        data = json.loads(text)
        top_type = type(data).__name__
        if isinstance(data, list):
            list_count = len(data)
    except Exception as e:
        err = str(e)
        top_type = f"json_error: {err}"

    return {
        "path": str(p),
        "exists": True,
        "size": len(b),
        "has_bom": bom,
        "top_type": top_type,
        "list_count": list_count,
        "head": head_text[:200] if head_text else None,
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


@app.get("/__debug/telegram_auth")
def debug_telegram_auth(x_telegram_init_data: str | None = Header(default=None)):
    ok, err, data = _telegram_check_hash(x_telegram_init_data or "", TELEGRAM_BOT_TOKEN)
    return {
        "ok": ok,
        "err": err,
        "bot_token_len": len(TELEGRAM_BOT_TOKEN or ""),
        "init_len": len(x_telegram_init_data or ""),
        "keys": data.get("keys") if isinstance(data, dict) else None,
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

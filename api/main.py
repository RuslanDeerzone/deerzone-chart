# =========================
# SONGS STORAGE (IRON MODE)
# =========================

from pathlib import Path
from typing import Any, Dict, List, Optional, Literal
import json
import os
import time
import traceback
from fastapi import Body, Header, HTTPException

# week_id фиксируем через env (у тебя сейчас 3)
CURRENT_WEEK_ID = int(os.getenv("CURRENT_WEEK_ID", "3"))

BASE_DIR = Path(__file__).resolve().parent  # /app/api
SONGS_PATH = BASE_DIR / "songs.json"
SONGS_BACKUP_PATH = BASE_DIR / "songs.backup.json"

# В памяти: { week_id: [song_dict, ...] }
SONGS_BY_WEEK: Dict[int, List[Dict[str, Any]]] = {}


def _atomic_write_json(path: Path, data: Any) -> None:
    """
    Атомарная запись JSON:
    1) пишем во временный файл
    2) replace() поверх целевого
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    raw = json.dumps(data, ensure_ascii=False, indent=2)

    # Пишем UTF-8 без BOM (стабильно для Linux/Railway)
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    """
    Железная загрузка списка из JSON.
    - читает utf-8-sig (BOM ок)
    - возвращает [] только если файла нет или JSON реально битый
    """
    if not path.exists():
        print(f"[BOOT] songs.json NOT FOUND: {path}", flush=True)
        return []

    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, list):
            print(f"[BOOT] songs.json is not list, got: {type(data)}", flush=True)
            return []
        # фильтруем только dict
        data = [x for x in data if isinstance(x, dict)]
        print(f"[BOOT] songs.json loaded: {len(data)} items", flush=True)
        return data
    except Exception as e:
        print("[BOOT] songs.json FAILED to load:", flush=True)
        print(traceback.format_exc(), flush=True)
        return []


def _normalize_song(s: Dict[str, Any]) -> Dict[str, Any]:
    """
    Приводим одну песню к нужным полям/типам.
    """
    out: Dict[str, Any] = {}

    # обязательные
    out["id"] = int(s.get("id", 0))  # 0 допустим, но лучше чтобы не было
    out["artist"] = str(s.get("artist", "")).strip()
    out["title"] = str(s.get("title", "")).strip()

    # опциональные
    out["weeks_in_chart"] = int(s.get("weeks_in_chart", 1) or 1)
    out["is_new"] = bool(s.get("is_new", False))
    out["source"] = str(s.get("source", "manual"))

    # cover/preview_url могут быть null
    out["cover"] = s.get("cover", None)
    out["preview_url"] = s.get("preview_url", None)

    # чистим пустые строки -> null
    if isinstance(out["cover"], str) and not out["cover"].strip():
        out["cover"] = None
    if isinstance(out["preview_url"], str) and not out["preview_url"].strip():
        out["preview_url"] = None

    return out


def _normalize_songs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    norm = [_normalize_song(x) for x in items]

    # выкидываем совсем пустые
    norm = [x for x in norm if x["artist"] and x["title"]]

    # проверяем id
    seen = set()
    bad_ids = []
    for x in norm:
        if x["id"] <= 0:
            bad_ids.append(x)
        if x["id"] in seen:
            bad_ids.append(x)
        seen.add(x["id"])

    if bad_ids:
        print("[BOOT] WARNING: some songs have bad/duplicate id (fix songs.json!)", flush=True)

    return norm


def _persist_week_songs(week_id: int, items: List[Dict[str, Any]], *, allow_empty: bool = False) -> None:
    """
    Сохранение songs.json с защитой:
    - делаем backup
    - запрещаем записывать пустой список (если allow_empty=False)
    """
    if (not allow_empty) and (len(items) == 0):
        raise RuntimeError("Refusing to overwrite songs.json with EMPTY list")

    # backup текущего, если есть
    if SONGS_PATH.exists():
        try:
            SONGS_BACKUP_PATH.write_text(SONGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            # backup не должен валить сервис
            print("[SAVE] WARNING: failed to write backup", flush=True)

    # пишем основной
    _atomic_write_json(SONGS_PATH, items)

    # обновляем память
    SONGS_BY_WEEK[week_id] = items

    print(f"[SAVE] songs.json persisted: week_id={week_id} count={len(items)}", flush=True)


def get_week_songs(week_id: int) -> List[Dict[str, Any]]:
    items = SONGS_BY_WEEK.get(week_id, [])
    return items if isinstance(items, list) else []


# ---------- STARTUP ----------
@app.on_event("startup")
def startup_event():
    items = _load_json_list(SONGS_PATH)
    items = _normalize_songs(items)

    # ВАЖНО: если файл есть, но нормализовалось в 0 — НЕ перезаписываем автоматически,
    # просто грузим как есть (пусто), чтобы не затирать backup.
    SONGS_BY_WEEK[CURRENT_WEEK_ID] = items

    print(f"[BOOT] CURRENT_WEEK_ID={CURRENT_WEEK_ID}", flush=True)
    print(f"[BOOT] SONGS_PATH={SONGS_PATH} exists={SONGS_PATH.exists()}", flush=True)
    print(f"[BOOT] SONGS_COUNT={len(items)}", flush=True)


# ---------- DEBUG ----------
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


# ---------- PUBLIC API ----------
@app.get("/weeks/{week_id}/songs")
def weeks_songs(
    week_id: int,
    filter: Literal["all", "new"] = "all",
    search: str = "",
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    # если у тебя есть строгая авторизация - оставь её тут как было
    # (мы не валим запросы, если initData пустой)
    items = get_week_songs(week_id)

    if filter == "new":
        items = [s for s in items if bool(s.get("is_new", False))]

    if search.strip():
        q = search.strip().lower()
        items = [s for s in items if q in (f"{s.get('artist','')} {s.get('title','')}".lower())]

    return items


# ---------- ADMIN: ENRICH + SAVE ----------
@app.post("/admin/weeks/current/songs/enrich")
def admin_enrich_current_week(
    force: bool = Body(default=False),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Подтягивает cover/preview_url (iTunes) и СРАЗУ сохраняет в songs.json.
    """
    try:
        # твоя проверка админ-токена (у тебя уже есть)
        require_admin(x_admin_token)

        week_id = CURRENT_WEEK_ID
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

                if (not cover) and res.get("cover"):
                    s["cover"] = res.get("cover")
                if (not preview) and res.get("preview_url"):
                    s["preview_url"] = res.get("preview_url")

                # считаем обновлением, если что-то появилось
                if (s.get("cover") != cover) or (s.get("preview_url") != preview):
                    updated += 1

            except Exception:
                errors += 1
                print("[ENRICH] error for:", s.get("artist"), "-", s.get("title"), flush=True)
                print(traceback.format_exc(), flush=True)

        # нормализуем и сохраняем (НЕ даём затереть пустым)
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

    except HTTPException:
        raise
    except Exception as e:
        print("❌ ENRICH FAILED", flush=True)
        print(traceback.format_exc(), flush=True)
        raise HTTPException(status_code=500, detail=str(e))

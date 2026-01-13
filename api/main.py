from __future__ import annotations

import os
import time
import hashlib
from typing import Optional, Literal, List, Dict

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

APP_NAME = "#deerzone chart API"

# Если DEV_ALLOW_NO_TELEGRAM=1 — разрешаем работать без initData (удобно для тестов)
DEV_ALLOW_NO_TELEGRAM = os.getenv("DEV_ALLOW_NO_TELEGRAM", "1") == "1"

# Токен бота не обязателен для запуска этой версии API.
# (Позже, когда будем делать нормальную проверку initData — пригодится)
BOT_TOKEN = os.getenv("BOT_TOKEN")  # может быть None


# -----------------------------------------------------------------------------
# DATA MODELS
# -----------------------------------------------------------------------------

class WeekOut(BaseModel):
    id: int
    title: str
    status: Literal["open", "closed"]


class SongOut(BaseModel):
    id: int
    artist: str
    title: str
    is_new: bool = False
    weeks_in_chart: int = 1  # сколько недель в чарте (для твоего правила 10 недель)


class VoteIn(BaseModel):
    song_ids: List[int] = Field(default_factory=list, description="Список ID песен (можно 1..N)")


class VoteOut(BaseModel):
    ok: bool
    week_id: int
    user_id: str
    voted_song_ids: List[int]


# -----------------------------------------------------------------------------
# IN-MEMORY STORAGE (простая и надёжная база для старта)
# -----------------------------------------------------------------------------

# Текущая неделя. Ты писал "Week 1 - 2026". У тебя сейчас в ответе API был week_id=3.
# Чтобы не ломать фронт — оставляем id=3, title=Week 1 - 2026, status=open
CURRENT_WEEK = WeekOut(id=3, title="Week 1 - 2026", status="open")

# Песни недели (35 штук: 10 + 25)
# ID делаем стабильными, чтобы голосование работало.
SONGS_BY_WEEK: Dict[int, List[SongOut]] = {
    3: [
        # --- Top-10 (carry over) ---
        SongOut(id=1, artist="EXO", title="I'm Home", is_new=False, weeks_in_chart=3),
        SongOut(id=2, artist="MINHO (SHINee)", title="TEMPO", is_new=False, weeks_in_chart=3),
        SongOut(id=3, artist="Stray Kids", title="Do It", is_new=False, weeks_in_chart=7),
        SongOut(id=4, artist="ITZY", title="TUNNEL VISION", is_new=False, weeks_in_chart=8),
        SongOut(id=5, artist="Stray Kids", title="DIVINE", is_new=False, weeks_in_chart=6),
        SongOut(id=6, artist="BABYMONSTER", title="PSYCHO", is_new=False, weeks_in_chart=5),
        SongOut(id=7, artist="Bang Chan (Stray Kids)", title="Roman Empire", is_new=False, weeks_in_chart=9),
        SongOut(id=8, artist="ALLDAY PROJECT", title="LOOK AT ME", is_new=False, weeks_in_chart=4),
        SongOut(id=9, artist="ILLIT", title="NOT CUTE ANYMORE", is_new=False, weeks_in_chart=6),
        SongOut(id=10, artist="Seonghwa (ATEEZ)", title="Skin", is_new=True, weeks_in_chart=1),

        # --- New entries (25) ---
        SongOut(id=11, artist="Re:Hearts", title="Persona", is_new=True, weeks_in_chart=1),
        SongOut(id=12, artist="Apink", title="Love Me More", is_new=True, weeks_in_chart=1),
        SongOut(id=13, artist="JOOHONEY (MONSTA X)", title="STING", is_new=True, weeks_in_chart=1),
        SongOut(id=14, artist="idntt", title="Pretty Boy Swag", is_new=True, weeks_in_chart=1),
        SongOut(id=15, artist="H1-KEY", title="The World Isn’t Like a Movie", is_new=True, weeks_in_chart=1),
        SongOut(id=16, artist="1MILLION", title="AT US", is_new=True, weeks_in_chart=1),
        SongOut(id=17, artist="CNBLUE", title="Killer Joy", is_new=True, weeks_in_chart=1),
        SongOut(id=18, artist="CHUU", title="XO, My Cyberlove", is_new=True, weeks_in_chart=1),
        SongOut(id=19, artist="BADA", title="Our Loud Goodby", is_new=True, weeks_in_chart=1),
        SongOut(id=20, artist="Shin Soohyun (UKISS)", title="Gray", is_new=True, weeks_in_chart=1),
        SongOut(id=21, artist="DynamicDuo", title="Watch It, Feel It (feat. Sunghoon of ENHYPEN)", is_new=True, weeks_in_chart=1),
        SongOut(id=22, artist="JIMIN", title="0108", is_new=True, weeks_in_chart=1),
        SongOut(id=23, artist="WAKER", title="LiKE THAT", is_new=True, weeks_in_chart=1),
        SongOut(id=24, artist="LATENCY", title="it was love", is_new=True, weeks_in_chart=1),
        SongOut(id=25, artist="ZICO X Crush", title="Yin and Yang | SMTM12 - PRODUCER CYPHER", is_new=True, weeks_in_chart=1),
        SongOut(id=26, artist="GRAY X Loco", title="PAPER | SMTM12 - PRODUCER CYPHER", is_new=True, weeks_in_chart=1),
        SongOut(id=27, artist="J-Tong X Hukky Shibaseki", title="Cockroaches | SMTM12 - PRODUCER CYPHER", is_new=True, weeks_in_chart=1),
        SongOut(id=28, artist="Lil Moshpit X Jay Park", title="GOAT | SMTM12 - PRODUCER CYPHER", is_new=True, weeks_in_chart=1),
        SongOut(id=29, artist="DIA (AWU)", title="Dance!", is_new=True, weeks_in_chart=1),
        SongOut(id=30, artist="ZEROBASONE", title="Running to Future", is_new=True, weeks_in_chart=1),
        SongOut(id=31, artist="COMMA", title="Toxic Sugar", is_new=True, weeks_in_chart=1),
        SongOut(id=32, artist="YOUNG POSSE × BENZO", title="LOSE YOUR SHXT", is_new=True, weeks_in_chart=1),
        SongOut(id=33, artist="Park Gunwook (ZEROBASEONE)", title="Day After Day", is_new=True, weeks_in_chart=1),
        SongOut(id=34, artist="HADES", title="Planet B", is_new=True, weeks_in_chart=1),
        SongOut(id=35, artist="HA SUNG WOONG", title="Tell The World", is_new=True, weeks_in_chart=1),
    ]
}

# Голоса: votes[week_id][song_id] = count
VOTES: Dict[int, Dict[int, int]] = {}

# Чтобы один человек не голосовал миллион раз: user_votes[week_id][user_id] = [song_ids]
USER_VOTES: Dict[int, Dict[str, List[int]]] = {}


# -----------------------------------------------------------------------------
# TELEGRAM AUTH (упрощённая)
# -----------------------------------------------------------------------------

def user_id_from_telegram_init_data(x_telegram_init_data: Optional[str]) -> str:
    """
    В боевом режиме тут делается проверка подписи initData (HMAC-SHA256) через BOT_TOKEN.
    Сейчас делаем стабильный user_id:
    - Если initData есть -> берём хэш строки
    - Если нет -> dev-user (если разрешено DEV_ALLOW_NO_TELEGRAM)
    """
    if not x_telegram_init_data:
        if DEV_ALLOW_NO_TELEGRAM:
            return "dev-user"
        raise HTTPException(status_code=401, detail="INIT_DATA_REQUIRED")

    # стабильный короткий id по initData (без парсинга)
    h = hashlib.sha256(x_telegram_init_data.encode("utf-8")).hexdigest()[:16]
    return f"tg-{h}"


def ensure_week_exists(week_id: int) -> None:
    if week_id not in SONGS_BY_WEEK:
        raise HTTPException(status_code=404, detail="WEEK_NOT_FOUND")


# -----------------------------------------------------------------------------
# APP
# -----------------------------------------------------------------------------

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для теста нормально
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# ROUTES
# -----------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "name": APP_NAME, "time": int(time.time())}


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
    items = SONGS_BY_WEEK[week_id]

    if filter == "new":
        items = [s for s in items if s.is_new]

    if search.strip():
        q = search.strip().lower()
        items = [
            s for s in items
            if q in (s.artist + " " + s.title).lower()
        ]

    # По умолчанию возвращаем как есть (у тебя там уже логика сортировок на фронте)
    return items


@app.get("/weeks/{week_id}/results")
def weeks_results(week_id: int):
    """
    Отдаёт текущие счётчики голосов.
    Это пригодится позже для админки/итогов.
    """
    ensure_week_exists(week_id)
    votes = VOTES.get(week_id, {})
    # Вернём в удобном виде: [{song_id, votes}]
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

    # проверяем, что такие песни вообще есть в этой неделе
    existing = {s.id for s in SONGS_BY_WEEK[week_id]}
    bad = [sid for sid in song_ids if sid not in existing]
    if bad:
        raise HTTPException(status_code=400, detail={"error": "INVALID_SONG_ID", "song_ids": bad})

    # ограничение на количество выборов (если нужно) — поставим мягко 10
    if len(song_ids) > 10:
        raise HTTPException(status_code=400, detail="TOO_MANY_SONGS_MAX_10")

    # анти-дубль: если уже голосовал — перезаписываем голос (сначала снимаем старые)
    USER_VOTES.setdefault(week_id, {})
    VOTES.setdefault(week_id, {})

    prev = USER_VOTES[week_id].get(user_id, [])
    for sid in prev:
        VOTES[week_id][sid] = max(0, VOTES[week_id].get(sid, 0) - 1)

    for sid in song_ids:
        VOTES[week_id][sid] = VOTES[week_id].get(sid, 0) + 1

    USER_VOTES[week_id][user_id] = song_ids

    return VoteOut(ok=True, week_id=week_id, user_id=user_id, voted_song_ids=song_ids)
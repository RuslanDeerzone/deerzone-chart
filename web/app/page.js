"use client";

import React, { useEffect, useMemo, useState } from "react";

/**
 * deerzone chart – web/app/page.js
 *
 * Что умеет:
 * - New / All фильтр (Returning убран)
 * - Сортировка: только A–Z (по артисту, затем по треку)
 * - Поиск
 * - Обложка (s.cover) + превью (s.preview_url) + fallback YouTube (s.youtube_url)
 * - Голосование: 1 аккаунт = 1 голосование (сервер перезаписывает), выбрать можно сколько угодно треков
 * - Ошибки 401/403 показываются понятно
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function getTelegramInitData() {
  try {
    // Telegram Mini App
    const tg = window.Telegram?.WebApp;
    if (!tg) return "";
    tg.ready?.();
    return tg.initData || "";
  } catch {
    return "";
  }
}

export default function Home() {
  const [initData, setInitData] = useState("");
  const [week, setWeek] = useState(null);

  const [filter, setFilter] = useState("all"); // all | new
  const [search, setSearch] = useState("");

  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(() => new Set());

  const [error, setError] = useState("");
  const [loadingWeek, setLoadingWeek] = useState(true);
  const [loadingSongs, setLoadingSongs] = useState(false);
  const [voting, setVoting] = useState(false);
  const [voteOk, setVoteOk] = useState("");

  // one-audio-at-a-time
  const [audioObj, setAudioObj] = useState(null);

  // INIT DATA
  useEffect(() => {
    setInitData(getTelegramInitData());
  }, []);

  // LOAD CURRENT WEEK
  useEffect(() => {
    setError("");
    setVoteOk("");
    setLoadingWeek(true);

    fetch(`${API_BASE}/weeks/current`, {
      headers: initData ? { "X-Telegram-Init-Data": initData } : {},
    })
      .then(async (r) => {
        if (r.status === 403) throw new Error("Нужна подписка на @deerzone");
        if (r.status === 401) throw new Error("Открой это через Telegram Mini App (initData отсутствует)");
        if (!r.ok) throw new Error("Не удалось загрузить неделю");
        return r.json();
      })
      .then((data) => {
        setWeek(data);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingWeek(false));
  }, [initData]);

  // LOAD SONGS
  useEffect(() => {
    if (!week?.id) return;

    setError("");
    setVoteOk("");
    setLoadingSongs(true);

    const url =
      `${API_BASE}/weeks/${week.id}/songs` +
      `?filter=${filter}` +
      `&search=${encodeURIComponent(search)}`;

    fetch(url, {
      headers: initData ? { "X-Telegram-Init-Data": initData } : {},
    })
      .then(async (r) => {
        if (r.status === 403) throw new Error("Нужна подписка на @deerzone");
        if (r.status === 401) throw new Error("Открой это через Telegram Mini App (initData отсутствует)");
        if (!r.ok) throw new Error("Не удалось загрузить песни");
        return r.json();
      })
      .then((data) => {
        setSongs(Array.isArray(data) ? data : []);
        // если список обновился — оставим selected только для существующих id
        const existing = new Set((Array.isArray(data) ? data : []).map((x) => x.id));
        setSelected((prev) => {
          const next = new Set();
          for (const id of prev) if (existing.has(id)) next.add(id);
          return next;
        });
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingSongs(false));
  }, [week, filter, search, initData]);

  // SORT A-Z (artist, then title)
  const sortedSongs = useMemo(() => {
    const arr = Array.isArray(songs) ? [...songs] : [];
    arr.sort((a, b) => {
      const aa = `${a.artist || ""}`.toLowerCase();
      const bb = `${b.artist || ""}`.toLowerCase();
      if (aa < bb) return -1;
      if (aa > bb) return 1;
      const at = `${a.title || ""}`.toLowerCase();
      const bt = `${b.title || ""}`.toLowerCase();
      if (at < bt) return -1;
      if (at > bt) return 1;
      return 0;
    });
    return arr;
  }, [songs]);

  function toggleSong(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function stopAudio() {
    try {
      if (audioObj) {
        audioObj.pause();
        audioObj.currentTime = 0;
      }
    } catch {}
    setAudioObj(null);
  }

  function playPreview(e, s) {
    e.stopPropagation();
    setVoteOk("");
    setError("");

    // нет превью → открыть YouTube fallback
    if (!s.preview_url) {
      if (s.youtube_url) window.open(s.youtube_url, "_blank");
      return;
    }

    // остановить предыдущий
    stopAudio();

    try {
      const a = new Audio(s.preview_url);
      a.play().catch(() => {
        if (s.youtube_url) window.open(s.youtube_url, "_blank");
      });

      // играем 15 секунд (можешь поменять на 10_000 или 30_000)
      const t = setTimeout(() => {
        try {
          a.pause();
          a.currentTime = 0;
        } catch {}
        clearTimeout(t);
      }, 15000);

      setAudioObj(a);
    } catch {
      if (s.youtube_url) window.open(s.youtube_url, "_blank");
    }
  }

  async function sendVote() {
    if (!week?.id) return;
    setVoteOk("");
    setError("");

    const ids = Array.from(selected);

    setVoting(true);
    try {
      const r = await fetch(`${API_BASE}/weeks/${week.id}/vote`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(initData ? { "X-Telegram-Init-Data": initData } : {}),
        },
        body: JSON.stringify({ song_ids: ids }),
      });

      if (r.status === 403) {
        const t = await r.json().catch(() => null);
        // может быть "VOTING_CLOSED" или "Нужна подписка..."
        throw new Error(typeof t?.detail === "string" ? t.detail : "Нужна подписка на @deerzone или голосование закрыто");
      }
      if (r.status === 401) throw new Error("Открой это через Telegram Mini App (initData отсутствует)");

      if (!r.ok) {
        const t = await r.json().catch(() => null);
        throw new Error(typeof t?.detail === "string" ? t.detail : "Ошибка при голосовании");
      }

      setVoteOk("Голос принят ✅ (если ты голосовал раньше — он обновлён)");
    } catch (e) {
      setError(e.message || "Ошибка при голосовании");
    } finally {
      setVoting(false);
    }
  }

  // UI
  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 18, fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Arial" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontSize: 26, fontWeight: 900, letterSpacing: -0.5 }}>#deerzone chart</div>
          <div style={{ marginTop: 4, opacity: 0.75, fontWeight: 600 }}>
            {loadingWeek ? "Загрузка недели..." : week ? week.title : "Неделя не загружена"}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => {
              stopAudio();
              setFilter("new");
            }}
            style={{
              padding: "10px 12px",
              borderRadius: 12,
              border: filter === "new" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 800,
            }}
          >
            New
          </button>

          <button
            onClick={() => {
              stopAudio();
              setFilter("all");
            }}
            style={{
              padding: "10px 12px",
              borderRadius: 12,
              border: filter === "all" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 800,
            }}
          >
            All
          </button>
        </div>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск (artist или title)"
          style={{
            flex: 1,
            padding: "12px 14px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            outline: "none",
            fontSize: 14,
          }}
        />

        <div style={{ padding: "12px 14px", borderRadius: 14, border: "1px solid #eaeaea", background: "#fff", fontWeight: 800 }}>
          A–Z
        </div>
      </div>

      {error ? (
        <div style={{ marginTop: 12, padding: 12, borderRadius: 14, border: "1px solid #ffd6e8", background: "rgba(255,63,164,0.06)", fontWeight: 700 }}>
          {error}
        </div>
      ) : null}

      {voteOk ? (
        <div style={{ marginTop: 12, padding: 12, borderRadius: 14, border: "1px solid #d8ffe7", background: "rgba(0,200,100,0.08)", fontWeight: 700 }}>
          {voteOk}
        </div>
      ) : null}

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {loadingSongs ? (
          <div style={{ opacity: 0.7, fontWeight: 700 }}>Загрузка песен...</div>
        ) : (
          Array.isArray(sortedSongs) &&
          sortedSongs.map((s) => {
            const isSel = selected.has(s.id);

            return (
              <div
                key={s.id}
                onClick={() => toggleSong(s.id)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "92px 1fr",
                  gap: 12,
                  padding: 12,
                  borderRadius: 16,
                  border: isSel ? "2px solid #ff3fa4" : "1px solid #eaeaea",
                  background: isSel ? "rgba(255,63,164,0.06)" : "#fff",
                  cursor: "pointer",
                }}
              >
                <div
                  style={{
                    width: 92,
                    height: 92,
                    borderRadius: 16,
                    background: "#f2f2f2",
                    overflow: "hidden",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {s.cover ? (
                    <img src={s.cover} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : null}
                </div>

                <div>
                  <div style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.1 }}>{s.title}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, opacity: 0.7, marginTop: 4 }}>{s.artist}</div>

                  <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button
                      onClick={(e) => playPreview(e, s)}
                      style={{
                        padding: "8px 12px",
                        borderRadius: 12,
                        border: "1px solid #eaeaea",
                        background: "#fff",
                        cursor: "pointer",
                        fontWeight: 800,
                      }}
                    >
                      ▶ Preview
                    </button>

                    {!s.preview_url && s.youtube_url ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          window.open(s.youtube_url, "_blank");
                        }}
                        style={{
                          padding: "8px 12px",
                          borderRadius: 12,
                          border: "1px solid #eaeaea",
                          background: "#fff",
                          cursor: "pointer",
                          fontWeight: 800,
                        }}
                      >
                        YouTube
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div style={{ marginTop: 16, display: "flex", gap: 10, alignItems: "center" }}>
        <button
          disabled={voting}
          onClick={sendVote}
          style={{
            flex: 1,
            padding: "14px 14px",
            borderRadius: 16,
            border: "1px solid #ff3fa4",
            background: "#ff3fa4",
            color: "#fff",
            cursor: voting ? "not-allowed" : "pointer",
            fontWeight: 900,
            fontSize: 16,
          }}
        >
          {voting ? "Отправляю..." : `VOTE (${selected.size})`}
        </button>

        <button
          onClick={() => {
            stopAudio();
            setSelected(new Set());
          }}
          style={{
            padding: "14px 14px",
            borderRadius: 16,
            border: "1px solid #eaeaea",
            background: "#fff",
            cursor: "pointer",
            fontWeight: 900,
          }}
        >
          Clear
        </button>
      </div>

      <div style={{ marginTop: 18, opacity: 0.6, fontSize: 12, lineHeight: 1.35 }}>
        В браузере может работать не полностью. В Telegram Mini App передаётся initData (это нужно для ограничений и подписки).
      </div>
    </div>
  );
}

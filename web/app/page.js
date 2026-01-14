"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

function getInitData() {
  if (typeof window === "undefined") return "";
  if (!window.Telegram) return "";
  if (!window.Telegram.WebApp) return "";
  return window.Telegram.WebApp.initData || "";
}

export default function Home() {
  const [week, setWeek] = useState(null);

  const [filter, setFilter] = useState("all"); // "all" | "new"
  const [search, setSearch] = useState("");

  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(new Set());

  const [error, setError] = useState("");

  // аудио превью
  const audioRef = useRef(null);
  const [playingId, setPlayingId] = useState(null); // song.id который сейчас играет

  const initData = useMemo(() => getInitData(), []);

  // 1) грузим текущую неделю
  useEffect(() => {
    (async () => {
      setError("");
      try {
        const r = await fetch(`${API_BASE}/weeks/current`, {
          headers: initData ? { "X-Telegram-Init-Data": initData } : {},
          cache: "no-store",
        });

        const parsed = await safeJson(r);

        if (!r.ok) {
          // если сервер вернул JSON — покажем detail, иначе raw
          const detail =
            parsed.ok && parsed.data?.detail
              ? parsed.data.detail
              : parsed.ok
              ? JSON.stringify(parsed.data)
              : parsed.text;
          throw new Error(`API ошибка (${r.status}): ${detail}`);
        }

        if (!parsed.ok) throw new Error("API вернул не-JSON");

        setWeek(parsed.data);
      } catch (e) {
        setError(e?.message || "API недоступен");
      }
    })();
  }, [initData]);

  // 2) грузим песни недели
  useEffect(() => {
  if (!week?.id) return;

  (async () => {
    try {
      setError("");

      if (!initData) {
        setError("Открой через Telegram");
        return;
      }

      const url = ${API_BASE}/weeks/${week.id}/songs?filter=${filter}&search=${encodeURIComponent(search)};

      const r = await fetch(url, {
        headers: {
          "X-Telegram-Init-Data": initData
        },
        cache: "no-store",
      });

      if (!r.ok) {
        setError("Load failed");
        return;
      }

      const data = await r.json();
      setSongs(Array.isArray(data) ? data : []);

    } catch (e) {
      console.error(e);
      setError("Load failed");
    }
  })();

}, [week, filter, search, initData]);

  // аккуратно останавливаем аудио при смене страницы/размонтаже
  useEffect(() => {
    return () => {
      try {
        if (audioRef.current) {
          audioRef.current.pause();
          audioRef.current = null;
        }
      } catch {}
    };
  }, []);

  function toggleSong(songId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(songId)) next.delete(songId);
      else next.add(songId);
      return next;
    });
  }

  async function togglePreview(song) {
    const url = song?.preview_url;
    if (!url) {
      alert("Нет превью для этой песни (пока).");
      return;
    }

    // если кликнули по той же песне — пауза/плей
    if (playingId === song.id && audioRef.current) {
      if (audioRef.current.paused) {
        try {
          await audioRef.current.play();
        } catch {
          alert("Не удалось запустить превью (браузер блокирует автозвук).");
        }
      } else {
        audioRef.current.pause();
      }
      return;
    }

    // иначе: останавливаем прошлое и включаем новое
    try {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }

      const a = new Audio(url);
      audioRef.current = a;
      setPlayingId(song.id);

      a.addEventListener("ended", () => {
        setPlayingId(null);
      });

      await a.play();
    } catch {
      setPlayingId(null);
      alert("Не удалось запустить превью (браузер блокирует автозвук).");
    }
  }

  async function vote() {
    if (!week?.id) return;
    setError("");

    const song_ids = Array.from(selected);

    try {
      const r = await fetch(`${API_BASE}/weeks/${week.id}/vote`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(initData ? { "X-Telegram-Init-Data": initData } : {}),
        },
        body: JSON.stringify({ song_ids }),
      });

      const parsed = await safeJson(r);

      if (!r.ok) {
        const detail =
          parsed.ok && parsed.data?.detail
            ? parsed.data.detail
            : parsed.ok
            ? JSON.stringify(parsed.data)
            : parsed.text;
        throw new Error(`Ошибка голосования (${r.status}): ${detail}`);
      }

      alert("Голос учтён ✅");
    } catch (e) {
      setError(e?.message || "Ошибка голосования");
    }
  }

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 16, fontFamily: "system-ui, -apple-system, Segoe UI, Roboto" }}>
      <div style={{ height: 8, background: "#ff3fa4", borderRadius: 999 }} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 18 }}>
        <div style={{ fontSize: 34, fontWeight: 900 }}>#deerzone chart</div>
        <div style={{ fontSize: 16, opacity: 0.6 }}>{week?.title || ""}</div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 18 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>
          Selected: <span style={{ fontWeight: 900 }}>{selected.size}</span>
        </div>

        <button
          onClick={vote}
          disabled={!week?.id}
          style={{
            padding: "10px 16px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 800,
            cursor: week?.id ? "pointer" : "not-allowed",
            opacity: week?.id ? 1 : 0.5,
          }}
        >
          VOTE
        </button>
      </div>

      <div style={{ marginTop: 14 }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search artist or title"
          style={{
            width: "100%",
            padding: "12px 14px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            outline: "none",
            fontSize: 16,
          }}
        />
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12 }}>
        <button
          onClick={() => setFilter("new")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: filter === "new" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: filter === "new" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          New
        </button>

        <button
          onClick={() => setFilter("all")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: filter === "all" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: filter === "all" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          All
        </button>

        <div style={{ marginLeft: "auto", opacity: 0.5, fontWeight: 700 }}>A–Z</div>
      </div>

      {error ? (
        <div
          style={{
            marginTop: 14,
            padding: 12,
            borderRadius: 14,
            border: "1px solid rgba(255,63,164,0.35)",
            background: "rgba(255,63,164,0.08)",
            color: "#222",
            fontWeight: 700,
          }}
        >
          {String(error)}
        </div>
      ) : null}

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.isArray(songs) &&
          songs.map((s) => {
            const isSel = selected.has(s.id);
            const isPlaying = playingId === s.id && audioRef.current && !audioRef.current.paused;

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
                {/* cover */}
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
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={s.cover}
                      alt=""
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    />
                  ) : (
                    <div style={{ fontSize: 12, opacity: 0.5, fontWeight: 800 }}>NO COVER</div>
                  )}
                </div>

                {/* info */}
                <div>
                  <div style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.15 }}>{s.title}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, opacity: 0.7, marginTop: 4 }}>{s.artist}</div>

                  <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 10 }}>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        togglePreview(s);
                      }}
                      style={{
                        padding: "8px 12px",
                        borderRadius: 12,
                        border: "1px solid #eaeaea",
                        background: "#fff",
                        cursor: "pointer",
                        fontWeight: 900,
                      }}
                    >
                      {isPlaying ? "⏸ Pause" : "▶ Preview"}
                    </button>

                    {!s.preview_url ? (
                      <span style={{ fontSize: 12, opacity: 0.55, fontWeight: 700 }}>
                        (превью нет)
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
      </div>

      <div style={{ marginTop: 18, opacity: 0.6, fontSize: 12 }}>
        Сейчас ты открыл это в браузере. Полноценно будет работать, когда откроешь как Telegram Mini App (нужно initData).
      </div>
    </div>
  );
}
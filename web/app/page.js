"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function getInitDataSafe() {
  if (typeof window === "undefined") return "";
  return window?.Telegram?.WebApp?.initData || "";
}

async function safeJson(r) {
  const text = await r.text();
  try {
    return { ok: true, data: JSON.parse(text) };
  } catch {
    return { ok: false, text };
  }
}

export default function Home() {
  const [week, setWeek] = useState(null);
  const [filter, setFilter] = useState("all"); // "all" | "new"
  const [search, setSearch] = useState("");
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState("");

  const [sendingVote, setSendingVote] = useState(false);
  const [voteMsg, setVoteMsg] = useState("");

  // audio preview
  const audioRef = useRef(null);
  const [playingId, setPlayingId] = useState(null);

  const initData = useMemo(() => getInitDataSafe(), []);

  // 1) грузим текущую неделю
  useEffect(() => {
    (async () => {
      setError("");
      try {
        const r = await fetch(`${API_BASE}/weeks/current`, {
          cache: "no-store",
          headers: initData ? { "X-Telegram-Init-Data": initData } : {},
        });

        const parsed = await safeJson(r);
        if (!r.ok) {
          const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
          setError(`API ошибка (${r.status}): ${detail}`);
          return;
        }
        if (!parsed.ok) {
          setError("API вернул не-JSON");
          return;
        }
        setWeek(parsed.data);
      } catch (e) {
        console.error(e);
        setError("Не удалось загрузить неделю");
      }
    })();
  }, [initData]);

  // 2) грузим песни недели
  useEffect(() => {
    if (!week?.id) return;

    (async () => {
      try {
        setError("");
        const f = filter === "new" ? "new" : "all";
        const q = typeof search === "string" ? search : "";

        const url = `${API_BASE}/weeks/${week.id}/songs?filter=${f}&search=${encodeURIComponent(
          q
        )}`;

        const r = await fetch(url, {
          cache: "no-store",
          headers: initData ? { "X-Telegram-Init-Data": initData } : {},
        });

        if (!r.ok) {
          const parsed = await safeJson(r);
          const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
          throw new Error(`HTTP ${r.status}: ${detail}`);
        }

        const data = await r.json();
        setSongs(Array.isArray(data) ? data : []);
      } catch (e) {
        console.error(e);
        setSongs([]);
        setError("Не удалось загрузить список");
      }
    })();
  }, [week?.id, filter, search, initData]);

  // аккуратно останавливаем аудио при размонтаже
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

  function toggleSong(id) {
    setVoteMsg("");
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function stopAudio() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    audioRef.current = null;
    setPlayingId(null);
  }

  async function playPreview(song) {
    try {
      if (playingId === song.id) {
        stopAudio();
        return;
      }

      stopAudio();

      if (song?.preview_url) {
        const a = new Audio(song.preview_url);
        audioRef.current = a;
        setPlayingId(song.id);
        a.play().catch(() => {
          setPlayingId(null);
          setError("Не удалось воспроизвести превью (браузер/Telegram блокирует звук).");
        });
        a.onended = () => {
          setPlayingId(null);
          audioRef.current = null;
        };
        return;
      }

      if (song?.youtube_url) {
        window.open(song.youtube_url, "_blank");
        return;
      }

      setError("Для этой песни нет preview_url и youtube_url");
    } catch (e) {
      console.error(e);
      setError("Ошибка проигрывания превью");
    }
  }

  async function submitVote() {
    try {
      setError("");
      setVoteMsg("");

      if (!week?.id) {
        setError("Нет текущей недели");
        return;
      }

      const ids = Array.from(selected);
      if (ids.length === 0) return;

      setSendingVote(true);

      const init = getInitDataSafe() || "dev";
      const url = `${API_BASE}/weeks/${week.id}/vote`;

      const r = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": init,
        },
        body: JSON.stringify({ song_ids: ids }),
      });

      const parsed = await safeJson(r);

      if (!r.ok) {
        const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
        setError(`Ошибка голосования (${r.status}): ${detail}`);
        return;
      }

      setVoteMsg("Голос учтён ✅");
    } catch (e) {
      console.error(e);
      setError("Не удалось отправить голос");
    } finally {
      setSendingVote(false);
    }
  }

  const selectedCount = selected.size;

  return (
    <div
      style={{
        maxWidth: 760,
        margin: "0 auto",
        padding: 18,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Inter, Arial, sans-serif",
      }}
    >
      <div style={{ height: 8, background: "#ff3fa4", borderRadius: 99 }} />

      <div style={{ marginTop: 18, fontSize: 44, fontWeight: 900 }}>#deerzone chart</div>

      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800 }}>Selected: {selectedCount}</div>

        <button
          disabled={selectedCount === 0 || sendingVote}
          style={{
            marginLeft: "auto",
            padding: "10px 14px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            background: selectedCount === 0 || sendingVote ? "#f5f5f5" : "#111",
            color: selectedCount === 0 || sendingVote ? "#999" : "#fff",
            cursor: selectedCount === 0 || sendingVote ? "not-allowed" : "pointer",
            fontWeight: 900,
          }}
          onClick={submitVote}
        >
          {sendingVote ? "SENDING..." : "VOTE"}
        </button>
      </div>

      {voteMsg ? (
        <div style={{ marginTop: 10, fontWeight: 900, opacity: 0.9 }}>{voteMsg}</div>
      ) : null}

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search artist or title"
        style={{
          marginTop: 14,
          width: "100%",
          padding: 14,
          borderRadius: 16,
          border: "1px solid #eaeaea",
          outline: "none",
          fontSize: 16,
        }}
      />

      <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
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

        <div style={{ marginLeft: "auto", opacity: 0.6, fontWeight: 800 }}>A–Z</div>
      </div>

      {error ? (
        <div
          style={{
            marginTop: 14,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffb3d8",
            background: "rgba(255,63,164,0.08)",
            fontWeight: 800,
          }}
        >
          {error}
        </div>
      ) : null}

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.isArray(songs) &&
          songs.map((s) => {
            const isSel = selected.has(s.id);
            const isPlaying = playingId === s.id;

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
                {s.cover ? (
                  <img
                    src={s.cover}
                    alt=""
                    width={92}
                    height={92}
                    style={{ width: 92, height: 92, borderRadius: 16, objectFit: "cover" }}
                  />
                ) : (
                  <div style={{ width: 92, height: 92, borderRadius: 16, background: "#f2f2f2" }} />
                )}

                <div>
                  <div style={{ fontSize: 18, fontWeight: 900 }}>{s.title}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, opacity: 0.7, marginTop: 2 }}>
                    {s.artist}
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      playPreview(s);
                    }}
                    style={{
                      marginTop: 10,
                      padding: "8px 12px",
                      borderRadius: 12,
                      border: "1px solid #eaeaea",
                      background: "#fff",
                      cursor: "pointer",
                      fontWeight: 900,
                    }}
                  >
                    {isPlaying ? "⏸ Stop" : "▶ Preview"}
                  </button>
                </div>
              </div>
            );
          })}
      </div>

      <div style={{ marginTop: 18, opacity: 0.6, fontSize: 12 }}>
        Если открыл не из Telegram — initData может быть пустой и часть функций будет ограничена.
      </div>
    </div>
  );
}
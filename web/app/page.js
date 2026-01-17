"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const LOGO_SRC = "/logo-deerzone.png"; // web/public/logo-deerzone.png

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

// сортировка: артист -> название (без учета регистра)
function sortByArtistThenTitle(a, b) {
  const aa = String(a?.artist || "").toLowerCase();
  const bb = String(b?.artist || "").toLowerCase();
  const t1 = String(a?.title || "").toLowerCase();
  const t2 = String(b?.title || "").toLowerCase();

  const c1 = aa.localeCompare(bb);
  if (c1 !== 0) return c1;
  return t1.localeCompare(t2);
}

export default function Home() {
  const [week, setWeek] = useState(null);

  // вкладка: all | new | current
  const [tab, setTab] = useState("all");

  const [search, setSearch] = useState("");
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState("");

  const [isVoting, setIsVoting] = useState(false);
  const [voteOk, setVoteOk] = useState(false);


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
        setError("Не удалось загрузить список");
      }
    })();
  }, [initData]);

  // 2) грузим песни недели (всегда filter=all, вкладки режем на фронте)
  useEffect(() => {
    if (!week?.id) return;

    (async () => {
      try {
        setError("");

        const q = typeof search === "string" ? search : "";
        const url = `${API_BASE}/weeks/${week.id}/songs?filter=all&search=${encodeURIComponent(q)}`;

        const r = await fetch(url, {
          cache: "no-store",
          headers: initData ? { "X-Telegram-Init-Data": initData } : {},
        });

        if (!r.ok) throw new Error(`HTTP ${r.status}`);

        const data = await r.json();
        setSongs(Array.isArray(data) ? data : []);
      } catch (e) {
        console.error(e);
        setSongs([]);
        setError("Не удалось загрузить список");
      }
    })();
  }, [week?.id, search, initData]);

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


async function submitVote() {
  if (!week?.id) return;

  // Если хочешь запретить голосование из обычного браузера — оставляем
  if (!initData) {
    setError("Голосование доступно только при открытии через Telegram (нужен initData).");
    return;
  }

  const songIds = Array.from(selected);
  const MAX = 10;

  if (songIds.length === 0) {
    setError("Выбери хотя бы одну песню.");
    return;
  }

  if (songIds.length > MAX) {
    setError(`Можно выбрать максимум ${MAX} песен.`);
    return;
  }

  try {
    setIsVoting(true);
    setError("");

    const r = await fetch(`${API_BASE}/weeks/${week.id}/vote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": initData,
      },
      body: JSON.stringify({ song_ids: songIds }),
    });

    const parsed = await safeJson(r);

    if (!r.ok) {
      const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
      setError(`Ошибка голосования (${r.status}): ${detail}`);
      return;
    }

    // успех
    setVoteOk(true);
    setSelected(new Set());
    alert("Голос принят ✅");
  } catch (e) {
    console.error(e);
    setError("Не удалось отправить голос. Проверь интернет/сервер.");
  } finally {
    setIsVoting(false);
  }
}


  async function playPreview(song) {
    try {
      if (playingId === song.id) {
        stopAudio();
        return;
      }

      stopAudio();

      if (song?.preview_url) {
        // ВАЖНО: preview_url может быть не только аудио, но и YouTube-ссылка.
        // Если это YouTube — откроем вкладку, а не Audio().
        const p = String(song.preview_url);
        if (p.includes("youtube.com") || p.includes("youtu.be")) {
          window.open(p, "_blank");
          return;
        }

        const a = new Audio(p);
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

      setError("Для этой песни нет preview_url");
    } catch (e) {
      console.error(e);
      setError("Ошибка проигрывания превью");
    }
  }

  const selectedCount = selected.size;

  
  // --- ФИЛЬТРАЦИЯ ВКЛАДОК ---
  const displaySongs = useMemo(() => {
    const list = Array.isArray(songs) ? [...songs] : [];

    let filtered = list;

    if (tab === "new") {
      filtered = filtered.filter((s) => !!s?.is_new);
    } else if (tab === "current") {
      // "остались в чарте с прошлой недели"
      // это надежнее всего определять по source === "carryover"
      filtered = filtered.filter((s) => String(s?.source || "") === "carryover");
    }

    // All / New / Current — сортируем по артисту
    filtered.sort(sortByArtistThenTitle);

    return filtered;
  }, [songs, tab]);

  return (
    <div
      style={{
        maxWidth: 760,
        margin: "0 auto",
        padding: 18,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Inter, Arial, sans-serif",
      }}
    >
      <div style={{ height: 8, background: "#ff3fa4", borderRadius: 99 }} />

{/* LOGO HEADER */}
<div
  style={{
    marginTop: 18,
    marginBottom: 12,
    display: "flex",
    justifyContent: "center",
  }}
>
  <img
    src="/logo-deerzone.png"
    alt="#deerzone chart"
    style={{
      width: "100%",
      maxWidth: 520,
      height: "auto",
      display: "block",
    }}
  />
</div>

<div
  style={{
    textAlign: "center",
    fontSize: 14,
    fontWeight: 800,
    opacity: 0.55,
    marginBottom: 10,
    letterSpacing: 0.5,
  }}
>
  weekly music chart
</div>

      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800 }}>Selected: {selectedCount}</div>

        <button
          disabled={selectedCount === 0|| isVoting}
          onClick={submitVote}
          style={{
            marginLeft: "auto",
            padding: "10px 14px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            background: selectedCount === 0 ? "#f5f5f5" : "#fff",
            cursor: selectedCount === 0 ? "not-allowed" : "pointer",
            fontWeight: 800,
            opacity: isVoting ? 0.6 : 1,
          }}
        >
          {isVoting ? "Sending..." : "VOTE"}
        </button>

      {voteOk ? (
        <div
          style={{
            marginTop: 14,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #c8f7d6",
            background: "rgba(0,200,100,0.08)",
            fontWeight: 800,
          }}
        >
          Голос принят ✅
        </div>
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

      {/* ВКЛАДКИ: All / New / Current */}
      <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
        <button
          onClick={() => setTab("all")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "all" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: tab === "all" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          All
        </button>

        <button
          onClick={() => setTab("new")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "new" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: tab === "new" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          New
        </button>

        <button
          onClick={() => setTab("current")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "current" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: tab === "current" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          Current
        </button>

        <div style={{ marginLeft: "auto", opacity: 0.6, fontWeight: 800 }}>Artist A–Z</div>
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
        {Array.isArray(displaySongs) &&
          displaySongs.map((s) => {
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
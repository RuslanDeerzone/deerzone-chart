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

function normStr(v) {
  return String(v || "").trim();
}
function artistKey(s) {
  return normStr(s?.artist).toLowerCase();
}
function titleKey(s) {
  return normStr(s?.title).toLowerCase();
}
function isYoutubeUrl(url) {
  const u = String(url || "");
  return u.includes("youtube.com") || u.includes("youtu.be");
}

export default function Home() {
  const [week, setWeek] = useState(null);

  // 🔑 Telegram initData — БЕЗ useState
  const initData =
    typeof window !== "undefined"
      ? window.Telegram?.WebApp?.initData || ""
      : "";

  console.log(
    "TG:",
    !!window?.Telegram,
    "WebApp:",
    !!window?.Telegram?.WebApp,
    "platform:",
    window?.Telegram?.WebApp?.platform || "n/a",
    "initDataLen:",
    initData.length
  );

  const [tab, setTab] = useState("all");
  const [search, setSearch] = useState("");
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState("");

  const [isVoting, setIsVoting] = useState(false);
  const [voteMsg, setVoteMsg] = useState("");

}, []);

  // audio preview
  const audioRef = useRef(null);
  const [playingId, setPlayingId] = useState(null);

  const voteTimer = useRef(null);

  function setFlash(msg) {
    if (voteTimer.current) clearTimeout(voteTimer.current);
    setVoteMsg(msg);
    voteTimer.current = setTimeout(() => setVoteMsg(""), 3000);
  }
  // 1) текущая неделя
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

  // 2) песни недели (на API current нет — берём all, а current фильтруем на фронте)
  useEffect(() => {
    if (!week?.id) return;

    (async () => {
      setError("");
      try {
        const filter = tab === "new" ? "new" : "all";
        const q = typeof search === "string" ? search : "";
        const url = `${API_BASE}/weeks/${week.id}/songs?filter=${filter}&search=${encodeURIComponent(
          q
        )}`;

        const r = await fetch(url, {
          cache: "no-store",
          headers: initData ? { "X-Telegram-Init-Data": initData } : {},
        });

        const parsed = await safeJson(r);
        if (!r.ok) {
          const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
          throw new Error(detail || `HTTP ${r.status}`);
        }
        if (!parsed.ok) throw new Error("API вернул не-JSON");

        setSongs(Array.isArray(parsed.data) ? parsed.data : []);
      } catch (e) {
        console.error(e);
        setSongs([]);
        setError("Не удалось загрузить список");
      }
    })();
  }, [week?.id, tab, search, initData]);

  // cleanup
  useEffect(() => {
    return () => {
      try {
        if (audioRef.current) audioRef.current.pause();
      } catch {}
      audioRef.current = null;
      if (voteTimer.current) clearTimeout(voteTimer.current);
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
      try {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
      } catch {}
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

      const p = song?.preview_url;

      // если превью = ютуб — открываем
      if (p && isYoutubeUrl(p)) {
        window.open(p, "_blank");
        return;
      }

      // если есть audio preview — играем
      if (p) {
        const a = new Audio(p);
        audioRef.current = a;
        setPlayingId(song.id);

        a.onended = () => {
          setPlayingId(null);
          audioRef.current = null;
        };

        a.play().catch(() => {
          setPlayingId(null);
          setError("Не удалось воспроизвести превью (Telegram/браузер блокирует звук).");
        });
        return;
      }

      // fallback: поиск YouTube
      const q = encodeURIComponent(`${song?.artist || ""} ${song?.title || ""}`.trim());
      window.open(`https://www.youtube.com/results?search_query=${q}`, "_blank");
    } catch (e) {
      console.error(e);
      setError("Ошибка проигрывания превью");
    }
  }

  const selectedCount = selected.size;

  const visibleSongs = useMemo(() => {
    const arr = Array.isArray(songs) ? [...songs] : [];

    // вкладка current: то, что осталось с прошлой недели
    if (tab === "current") {
      const only = arr.filter((s) => {
        const src = String(s?.source || "").toLowerCase();
        if (src.includes("carry")) return true;
        if (src === "new") return false;
        return !Boolean(s?.is_new);
      });

      only.sort((a, b) => {
        const ak = artistKey(a);
        const bk = artistKey(b);
        if (ak !== bk) return ak.localeCompare(bk);
        return titleKey(a).localeCompare(titleKey(b));
      });

      return only;
    }

    // all/new — всегда сортируем по артисту
    arr.sort((a, b) => {
      const ak = artistKey(a);
      const bk = artistKey(b);
      if (ak !== bk) return ak.localeCompare(bk);
      return titleKey(a).localeCompare(titleKey(b));
    });

    return arr;
  }, [songs, tab]);

  async function submitVote() {
    if (!week?.id) return;

    // только из Telegram Mini App
    if (!initData) {
      setError("Голосование доступно только при открытии через Telegram (нужен initData).");
      return;
    }

    const songIds = Array.from(selected);
    if (songIds.length === 0) return;

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

        if (r.status === 401) {
          setError("Telegram auth не найден. Открой мини-апп из Telegram.");
        } else if (r.status === 409) {
          setError("Ты уже голосовал на этой неделе.");
          setFlash("Ты уже голосовал ✅");
        } else {
          setError(`Ошибка голосования (${r.status}): ${detail}`);
        }
        return;
      }

      // успех
      setSelected(new Set());
      setFlash("Голос принят ✅");
    } catch (e) {
      console.error(e);
      setError("Не удалось отправить голос. Проверь интернет/сервер.");
    } finally {
      setIsVoting(false);
    }
  }

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

      {/* ЛОГО-ШАПКА: замени src на свой путь */}
      <div style={{ marginTop: 16, display: "flex", justifyContent: "center" }}>
        <img
          src="/logo.png"
          alt="#deerzone chart"
          style={{
            width: "100%",
            maxWidth: 560,
            height: "auto",
            objectFit: "contain",
          }}
        />
      </div>

      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.6 }}>
        tg: {String(!!window?.Telegram)} ·
        webapp: {String(!!window?.Telegram?.WebApp)} ·
        platform: {window?.Telegram?.WebApp?.platform || "n/a"} ·
        initDataLen: {window?.Telegram?.WebApp?.initData?.length || 0}
      </div>

      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 900 }}>Selected: {selectedCount}</div>

        <button
          disabled={selectedCount === 0 || isVoting}
          onClick={submitVote}
          style={{
            marginLeft: "auto",
            padding: "10px 14px",
            borderRadius: 14,
            border: "1px solid #eaeaea",
            background: selectedCount === 0 ? "#f5f5f5" : "#fff",
            cursor: selectedCount === 0 ? "not-allowed" : "pointer",
            fontWeight: 900,
            opacity: isVoting ? 0.6 : 1,
          }}
        >
          {isVoting ? "Sending..." : "VOTE"}
        </button>
      </div>

      {/* Сообщение успеха/повтора */}
      {voteMsg ? (
        <div
          style={{
            marginTop: 14,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #c8f7d6",
            background: "rgba(0,200,100,0.08)",
            fontWeight: 900,
          }}
        >
          {voteMsg}
        </div>
      ) : null}

      {/* Ошибка */}
      {error ? (
        <div
          style={{
            marginTop: 14,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffb3d8",
            background: "rgba(255,63,164,0.08)",
            fontWeight: 900,
          }}
        >
          {error}
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

      <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
        <button
          onClick={() => setTab("all")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "all" ? "2px solid #ff3fa4" : "1px solid #eaeaea",
            background: tab === "all" ? "rgba(255,63,164,0.08)" : "#fff",
            cursor: "pointer",
            fontWeight: 900,
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
            fontWeight: 900,
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
            fontWeight: 900,
          }}
        >
          Current
        </button>

        <div style={{ marginLeft: "auto", opacity: 0.6, fontWeight: 900 }}>
          Artist A–Z
        </div>
      </div>

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.isArray(visibleSongs) &&
          visibleSongs.map((s) => {
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
                  <div style={{ fontSize: 14, fontWeight: 800, opacity: 0.7, marginTop: 2 }}>
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
    </div>
  );
}
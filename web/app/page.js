"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  "https://sincere-perception-production-65ac.up.railway.app/api"; // если у тебя API на другом URL — поправь только это

function safeJson(res) {
  return res
    .json()
    .then((data) => ({ ok: true, data }))
    .catch(async () => ({ ok: false, text: await res.text().catch(() => "") }));
}

export default function Home() {
  const [week, setWeek] = useState(null);

  // вкладка: all | new | current
  const [tab, setTab] = useState("all");

  const [search, setSearch] = useState("");
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [error, setError] = useState("");

  const [isVoting, setIsVoting] = useState(false);
  const [voteMsg, setVoteMsg] = useState("");

  // чтобы не было hydration mismatch
  const [mounted, setMounted] = useState(false);

  // telegram debug info
  const [tgInfo, setTgInfo] = useState({
    tg: false,
    webapp: false,
    platform: "n/a",
    initLen: 0,
  });

  // initData (один-единственный источник правды)
  const [initData, setInitData] = useState("");

  // audio preview
  const audioRef = useRef(null);
  const [playingId, setPlayingId] = useState(null);

  // авто-скрытие сообщений
  const voteTimer = useRef(null);
  function flash(msg) {
    if (voteTimer.current) clearTimeout(voteTimer.current);
    setVoteMsg(msg);
    voteTimer.current = setTimeout(() => setVoteMsg(""), 3000);
  }

  useEffect(() => {
    setMounted(true);

    const w = typeof window !== "undefined" ? window : null;
    const tg = !!w?.Telegram;
    const webapp = !!w?.Telegram?.WebApp;
    const platform = w?.Telegram?.WebApp?.platform || "n/a";
    const init = w?.Telegram?.WebApp?.initData || "";

    setInitData(init);
    setTgInfo({ tg, webapp, platform, initLen: init.length });

    try {
      w?.Telegram?.WebApp?.ready?.();
      w?.Telegram?.WebApp?.expand?.();
    } catch {}
  }, []);

  // --- helpers: сортировка/фильтр ---
  const selectedCount = selected.size;

  const normalizedSearch = search.trim().toLowerCase();

  const filteredSongs = useMemo(() => {
    let list = Array.isArray(songs) ? songs.slice() : [];

    // вкладки
    if (tab === "new") {
      list = list.filter((s) => !!s.is_new); // ожидаем поле is_new
    } else if (tab === "current") {
      list = list.filter((s) => !!s.is_current); // ожидаем поле is_current
    }

    // поиск
    if (normalizedSearch) {
      list = list.filter((s) => {
        const a = String(s.artist || "").toLowerCase();
        const t = String(s.title || "").toLowerCase();
        return a.includes(normalizedSearch) || t.includes(normalizedSearch);
      });
    }

    // All всегда по артистам A–Z (и внутри по title)
    list.sort((x, y) => {
      const ax = String(x.artist || "").toLowerCase();
      const ay = String(y.artist || "").toLowerCase();
      if (ax < ay) return -1;
      if (ax > ay) return 1;
      const tx = String(x.title || "").toLowerCase();
      const ty = String(y.title || "").toLowerCase();
      if (tx < ty) return -1;
      if (tx > ty) return 1;
      return 0;
    });

    return list;
  }, [songs, tab, normalizedSearch]);

  // --- API: загрузка недели и песен ---
  async function loadWeekAndSongs() {
    try {
      setError("");

      // 1) текущая неделя
      const wRes = await fetch(`${API_BASE}/weeks/current`);
      const wParsed = await safeJson(wRes);
      if (!wRes.ok) {
        setError(`Не удалось загрузить неделю (${wRes.status})`);
        return;
      }
      const w = wParsed.ok ? wParsed.data : null;
      setWeek(w);

      // 2) песни недели
      const id = w?.id;
      if (!id) {
        setSongs([]);
        return;
      }

      const sRes = await fetch(`${API_BASE}/weeks/${id}/songs`);
      const sParsed = await safeJson(sRes);
      if (!sRes.ok) {
        setError(`Не удалось загрузить песни (${sRes.status})`);
        return;
      }

      const items = sParsed.ok ? sParsed.data : [];
      setSongs(Array.isArray(items) ? items : []);
    } catch (e) {
      console.error(e);
      setError("Ошибка загрузки. Проверь сервер/интернет.");
    }
  }

  useEffect(() => {
    loadWeekAndSongs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- selection ---
  function toggleSong(s) {
    const id = s?.id;
    if (!id) return;

    setSelected((prev) => {
      const next = new Set(prev);

      // если добавляем 11-ю — запрещаем
      if (!next.has(id) && next.size >= 10) {
        flash("Можно выбрать максимум 10 треков");
        return next;
      }

      if (next.has(id)) next.delete(id);
      else next.add(id);

      return next;
    });
  }

  // --- audio preview ---
  function playPreview(s) {
    const url = s?.preview_url;
    const id = s?.id;
    if (!url || !id) return;

    try {
      if (playingId === id) {
        audioRef.current?.pause?.();
        setPlayingId(null);
        return;
      }

      if (!audioRef.current) {
        audioRef.current = new Audio(url);
      } else {
        audioRef.current.pause?.();
        audioRef.current.src = url;
      }

      audioRef.current.onended = () => setPlayingId(null);
      audioRef.current.play?.();
      setPlayingId(id);
    } catch (e) {
      console.error(e);
      flash("Не удалось воспроизвести превью");
    }
  }

  // --- vote ---
  async function submitVote() {
    if (!week?.id) return;

    const songIds = Array.from(selected);

    if (songIds.length === 0) return;

    // локальная защита 10 (даже если где-то в UI проскочит)
    if (songIds.length > 10) {
      flash("Можно выбрать максимум 10 треков");
      return;
    }

    if (!initData) {
      flash("Голосование доступно только при открытии через Telegram (нужен initData).");
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

        // красивые типовые ответы
        if (r.status === 401) {
          flash("Telegram auth не найден. Открой мини-апп из Telegram.");
        } else if (r.status === 409) {
          flash("Ты уже голосовал на этой неделе.");
        } else if (r.status === 400 && detail.includes("TOO_MANY_SONGS_MAX_10")) {
          flash("Можно выбрать максимум 10 треков");
        } else {
          flash(`Ошибка голосования (${r.status})`);
          setError(`Ошибка голосования (${r.status}): ${detail}`);
        }
        return;
      }

      // успех
      setSelected(new Set());
      flash("Голос принят ✅");
    } catch (e) {
      console.error(e);
      flash("Не удалось отправить голос. Проверь интернет/сервер.");
    } finally {
      setIsVoting(false);
    }
  }

  const disabledVote = selectedCount === 0 || selectedCount > 10 || isVoting;

  return (
    <div style={{ padding: 16, maxWidth: 720, margin: "0 auto" }}>
      <div style={{ height: 8, borderRadius: 999, background: "#ff3aa3", marginBottom: 14 }} />

      {/* LOGO */}
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 14 }}>
        <img
          src="/logo-deerzone.png"
          alt="#deerzone chart"
          style={{ maxWidth: 520, width: "100%", height: "auto" }}
        />
      </div>

      {/* debug (только после mount, чтобы не было hydration error) */}
      {mounted ? (
        <div style={{ marginTop: 6, marginBottom: 10, fontSize: 12, opacity: 0.6, textAlign: "center" }}>
          tg: {String(tgInfo.tg)} · webapp: {String(tgInfo.webapp)} · platform: {tgInfo.platform} · initDataLen:{" "}
          {tgInfo.initLen}
        </div>
      ) : null}

      {/* top row */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <div style={{ fontSize: 36, fontWeight: 900, letterSpacing: -1 }}>
          Selected: {selectedCount}
        </div>

        <button
          onClick={submitVote}
          disabled={disabledVote}
          style={{
            marginLeft: "auto",
            padding: "12px 18px",
            borderRadius: 16,
            border: "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: disabledVote ? "not-allowed" : "pointer",
            opacity: disabledVote ? 0.55 : 1,
          }}
        >
          {isVoting ? "Sending..." : "VOTE"}
        </button>
      </div>

      {/* flash message */}
      {voteMsg ? (
        <div
          style={{
            marginTop: 10,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffd1dc",
            background: "rgba(255,0,80,0.06)",
            fontWeight: 800,
          }}
        >
          {voteMsg}
        </div>
      ) : null}

      {/* error (debug details) */}
      {error ? (
        <div
          style={{
            marginTop: 10,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffd1dc",
            background: "rgba(255,0,80,0.06)",
            fontWeight: 800,
            whiteSpace: "pre-wrap",
          }}
        >
          {error}
        </div>
      ) : null}

      {/* search */}
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search artist or title"
        style={{
          width: "100%",
          marginTop: 14,
          padding: "14px 16px",
          borderRadius: 16,
          border: "1px solid #eaeaea",
          outline: "none",
          fontSize: 16,
        }}
      />

      {/* tabs */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
        <button
          onClick={() => setTab("all")}
          style={{
            padding: "10px 16px",
            borderRadius: 999,
            border: tab === "all" ? "2px solid #ff3aa3" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
            color: tab === "all" ? "#ff3aa3" : "#1b1b1b",
          }}
        >
          All
        </button>

        <button
          onClick={() => setTab("new")}
          style={{
            padding: "10px 16px",
            borderRadius: 999,
            border: tab === "new" ? "2px solid #ff3aa3" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
            color: tab === "new" ? "#ff3aa3" : "#1b1b1b",
          }}
        >
          New
        </button>

        <button
          onClick={() => setTab("current")}
          style={{
            padding: "10px 16px",
            borderRadius: 999,
            border: tab === "current" ? "2px solid #ff3aa3" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
            color: tab === "current" ? "#ff3aa3" : "#1b1b1b",
          }}
        >
          Current
        </button>

        <div style={{ marginLeft: "auto", opacity: 0.6, fontWeight: 900 }}>Artist A–Z</div>
      </div>

      {/* list */}
      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 12 }}>
        {filteredSongs.map((s) => {
          const isSelected = selected.has(s.id);
          const isPlaying = playingId === s.id;

          return (
            <div
              key={s.id}
              onClick={() => toggleSong(s)}
              style={{
                display: "flex",
                gap: 14,
                padding: 14,
                borderRadius: 18,
                border: isSelected ? "2px solid #ff3aa3" : "1px solid #eaeaea",
                background: "#fff",
                cursor: "pointer",
              }}
            >
              {s.cover ? (
                <img
                  src={s.cover}
                  alt=""
                  style={{ width: 92, height: 92, borderRadius: 16, objectFit: "cover" }}
                />
              ) : (
                <div style={{ width: 92, height: 92, borderRadius: 16, background: "#f2f2f2" }} />
              )}

              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 18, fontWeight: 900 }}>{s.title}</div>
                <div style={{ fontSize: 14, fontWeight: 800, opacity: 0.7, marginTop: 2 }}>{s.artist}</div>

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
                    cursor: s.preview_url ? "pointer" : "not-allowed",
                    fontWeight: 900,
                    opacity: s.preview_url ? 1 : 0.4,
                  }}
                  disabled={!s.preview_url}
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

"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  "https://melodious-courtesy-production-88f3.up.railway.app"; // ✅ ЭТО API (не WEB)

function safeJson(r) {
  return r
    .json()
    .then((data) => ({ ok: true, data }))
    .catch(() => r.text().then((text) => ({ ok: false, text })));
}

function normalize(s) {
  return String(s || "").trim().toLowerCase();
}

function getInitDataFromUrl() {
  if (typeof window === "undefined") return "";
  const sp = new URLSearchParams(window.location.search);
  const v = sp.get("tgWebAppData");
  return v ? decodeURIComponent(v) : "";
}

function getPlatformFromUrl() {
  if (typeof window === "undefined") return "";
  const sp = new URLSearchParams(window.location.search);
  return sp.get("tgWebAppPlatform") || "";
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
  const [voteMsg, setVoteMsg] = useState("");

  const voteTimer = useRef(null);
  function flash(msg) {
    if (voteTimer.current) clearTimeout(voteTimer.current);
    setVoteMsg(msg);
    voteTimer.current = setTimeout(() => setVoteMsg(""), 3000);
  }

  // ✅ Telegram initData + инфа о среде (железно после mount)
  const [mounted, setMounted] = useState(false);
  const [initData, setInitData] = useState("");
  const [tgInfo, setTgInfo] = useState({
    tg: false,
    webapp: false,
    platform: "n/a",
    initLen: 0,
  });

  useEffect(() => {
    setMounted(true);

    const w = typeof window !== "undefined" ? window : null;
    const tg = !!w?.Telegram;
    const webapp = !!w?.Telegram?.WebApp;

    const init = (w?.Telegram?.WebApp?.initData || "") || getInitDataFromUrl();
    const platform =
      w?.Telegram?.WebApp?.platform || getPlatformFromUrl() || "n/a";

    setInitData(init);
    setTgInfo({ tg, webapp, platform, initLen: init.length });

    try {
      w?.Telegram?.WebApp?.ready?.();
      w?.Telegram?.WebApp?.expand?.();
    } catch {}
  }, []);

  // загрузка текущей недели + песен
  useEffect(() => {
    (async () => {
      try {
        setError("");

        const w = await fetch(`${API_BASE}/weeks/current`);
        const wj = await safeJson(w);
        if (!w.ok) {
          setError(
            `Не удалось получить current week (${w.status}): ${
              wj.ok ? JSON.stringify(wj.data) : wj.text
            }`
          );
          return;
        }
        setWeek(wj.data);

        const s = await fetch(`${API_BASE}/weeks/${wj.data.id}/songs`);
        const sj = await safeJson(s);
        if (!s.ok) {
          setError(
            `Не удалось получить songs (${s.status}): ${
              sj.ok ? JSON.stringify(sj.data) : sj.text
            }`
          );
          return;
        }
        setSongs(Array.isArray(sj.data) ? sj.data : []);
      } catch (e) {
        console.error(e);
        setError("Ошибка сети при загрузке данных.");
      }
    })();
  }, []);

  const selectedCount = selected.size;

  // сортировка All: по артисту A–Z
  const baseList = useMemo(() => {
    const list = Array.isArray(songs) ? songs.slice() : [];
    list.sort((a, b) => {
      const aa = normalize(a.artist);
      const bb = normalize(b.artist);
      if (aa < bb) return -1;
      if (aa > bb) return 1;

      const at = normalize(a.title);
      const bt = normalize(b.title);
      if (at < bt) return -1;
      if (at > bt) return 1;
      return 0;
    });
    return list;
  }, [songs]);

  const filtered = useMemo(() => {
    const q = normalize(search);
    let list = baseList;

    if (tab === "new") list = list.filter((s) => !!s.is_new);
    if (tab === "current") list = list.filter((s) => !!s.is_current);

    if (!q) return list;
    return list.filter((s) => {
      const a = normalize(s.artist);
      const t = normalize(s.title);
      return a.includes(q) || t.includes(q);
    });
  }, [baseList, search, tab]);

  // audio preview
  const audioRef = useRef(null);
  const [playingId, setPlayingId] = useState(null);

  function playPreview(song) {
    const url = song?.preview_url;
    if (!url) return;

    if (playingId === song.id && audioRef.current) {
      audioRef.current.pause();
      setPlayingId(null);
      return;
    }

    try {
      if (!audioRef.current) audioRef.current = new Audio();
      audioRef.current.pause();
      audioRef.current.src = url;
      audioRef.current.currentTime = 0;
      audioRef.current.play();
      setPlayingId(song.id);
      audioRef.current.onended = () => setPlayingId(null);
    } catch (e) {
      console.error(e);
      setPlayingId(null);
    }
  }

  // ✅ лимит 20 прямо на выборе
  function toggleSong(id) {
    setSelected((prev) => {
      const next = new Set(prev);

      if (next.has(id)) {
        next.delete(id);
        return next;
      }

      if (next.size >= 20) {
        flash("Максимум 20 треков за голосование.");
        return next;
      }

      next.add(id);
      return next;
    });
  }

  async function submitVote() {
    if (!week?.id) return;

    const songIds = Array.from(selected);
    if (songIds.length === 0) return;

    // ✅ лимит 20 на всякий случай
    if (songIds.length > 20) {
      flash("Максимум 20 треков за голосование.");
      setError("Выбрано больше 20 треков. Убери лишние.");
      return;
    }

    if (!initData) {
      flash("initData пустой — Telegram WebApp не инициализировался.");
      setError("initData пустой. Открой мини-апп внутри Telegram.");
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
        if (r.status === 401) {
          flash("Telegram auth не принят сервером (401).");
          setError("401: Сервер не принял Telegram initData (проверь валидацию на API).");
        } else if (r.status === 409) {
          flash("Ты уже голосовал на этой неделе.");
          setError("Ты уже голосовал на этой неделе.");
        } else {
          const detail = parsed.ok ? JSON.stringify(parsed.data) : parsed.text;
          flash(`Ошибка голосования (${r.status})`);
          setError(`Ошибка голосования (${r.status}): ${detail}`);
        }
        return;
      }

      setSelected(new Set());
      flash("Голос принят ✅");
    } catch (e) {
      console.error(e);
      flash("Не удалось отправить голос.");
      setError("Не удалось отправить голос. Проверь интернет/сервер.");
    } finally {
      setIsVoting(false);
    }
  }

  return (
    <div style={{ padding: 16, maxWidth: 740, margin: "0 auto" }}>
      <div
        style={{
          height: 8,
          borderRadius: 999,
          background: "#ff3aa7",
          marginBottom: 14,
        }}
      />

      <div style={{ textAlign: "center", marginBottom: 14 }}>
        <img
          src="/logo-deerzone.png"
          alt="#deerzone chart"
          style={{ maxWidth: "100%", height: "auto" }}
        />
      </div>

      {/* debug строка (только после mount) */}
      {mounted ? (
        <div style={{ marginTop: 8, fontSize: 12, opacity: 0.7 }}>
          tg: {String(tgInfo.tg)} · webapp: {String(tgInfo.webapp)} · platform:{" "}
          {tgInfo.platform} · initDataLen: {tgInfo.initLen}
        </div>
      ) : null}


      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
        <div style={{ fontSize: 28, fontWeight: 900 }}>Selected: {selectedCount}</div>

        <button
          disabled={selectedCount === 0 || isVoting}
          onClick={submitVote}
          style={{
            marginLeft: "auto",
            padding: "12px 18px",
            borderRadius: 16,
            border: "1px solid #eaeaea",
            background: "#fff",
            cursor: selectedCount === 0 ? "not-allowed" : "pointer",
            fontWeight: 900,
            opacity: isVoting ? 0.6 : 1,
          }}
        >
          {isVoting ? "Sending..." : "VOTE"}
        </button>
      </div>

      {voteMsg ? (
        <div
          style={{
            marginTop: 12,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffd1e8",
            background: "rgba(255,58,167,0.08)",
            fontWeight: 900,
          }}
        >
          {voteMsg}
        </div>
      ) : null}

      {error ? (
        <div
          style={{
            marginTop: 12,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #ffd1d1",
            background: "rgba(255,0,0,0.06)",
            fontWeight: 800,
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
          marginTop: 16,
          width: "100%",
          padding: "14px 16px",
          borderRadius: 16,
          border: "1px solid #eaeaea",
          outline: "none",
          fontSize: 16,
        }}
      />

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
        <button
          onClick={() => setTab("all")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "all" ? "2px solid #ff3aa7" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
          }}
        >
          All
        </button>

        <button
          onClick={() => setTab("new")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "new" ? "2px solid #ff3aa7" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
          }}
        >
          New
        </button>

        <button
          onClick={() => setTab("current")}
          style={{
            padding: "10px 14px",
            borderRadius: 999,
            border: tab === "current" ? "2px solid #ff3aa7" : "1px solid #eaeaea",
            background: "#fff",
            fontWeight: 900,
            cursor: "pointer",
          }}
        >
          Current
        </button>

        <div style={{ marginLeft: "auto", opacity: 0.6, fontWeight: 900 }}>
          Artist A–Z
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        {filtered.map((s) => {
          const isSelected = selected.has(s.id);
          const isPlaying = playingId === s.id;

          return (
            <div
              key={s.id}
              onClick={() => toggleSong(s.id)}
              style={{
                display: "flex",
                gap: 14,
                padding: 14,
                borderRadius: 18,
                border: isSelected ? "2px solid #ff3aa7" : "1px solid #eaeaea",
                marginBottom: 12,
                cursor: "pointer",
                background: "#fff",
              }}
            >
              {s.cover ? (
                <img
                  src={s.cover}
                  alt=""
                  style={{
                    width: 92,
                    height: 92,
                    borderRadius: 16,
                    objectFit: "cover",
                  }}
                />
              ) : (
                <div
                  style={{
                    width: 92,
                    height: 92,
                    borderRadius: 16,
                    background: "#f2f2f2",
                  }}
                />
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
    </div>
  );
}
"use client";

import { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function getInitData() {
  return window?.Telegram?.WebApp?.initData || "";
}

export default function Home() {
  const [week, setWeek] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState("");

  const initData = useMemo(() => getInitData(), []);

  useEffect(() => {
    fetch(`${API_BASE}/weeks/current`)
      .then(r => r.json())
      .then(setWeek)
      .catch(() => setError("API недоступен"));
  }, []);

  useEffect(() => {
    if (!week) return;
    setError("");

    fetch(`${API_BASE}/weeks/${week.id}/songs?filter=${filter}&search=${encodeURIComponent(search)}`, {
      headers: { "X-Telegram-Init-Data": initData }
    })
      .then(async (r) => {
        if (r.status === 403) throw new Error("Нужна подписка на @deerzone");
        if (r.status === 401) throw new Error("Открой это через Telegram Mini App (initData отсутствует)");
        return r.json();
      })
      .then(setSongs)
      .catch(e => setError(e.message));
  }, [week, filter, search, initData]);

  function toggleSong(id) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  async function submitVote() {
    setError("");
    if (!week) return;
    if (selected.size < 1) {
      setError("Выбери хотя бы 1 песню");
      return;
    }
    const song_ids = Array.from(selected);

    const r = await fetch(`${API_BASE}/weeks/${week.id}/vote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": initData
      },
      body: JSON.stringify({ song_ids })
    });

    if (r.status === 409) { setError("Ты уже голосовал на этой неделе"); return; }
    if (r.status === 403) { setError("Нужна подписка на @deerzone"); return; }
    if (!r.ok) { setError("Ошибка отправки голоса"); return; }

    setSelected(new Set());
    alert("Голос принят ✅");
  }

  return (
    <div style={{ maxWidth: 520, margin: "0 auto", padding: 16, fontFamily: "system-ui" }}>
      <div style={{ height: 8, background: "#ff3fa4", borderRadius: 8, marginBottom: 12 }} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>#deerzone chart</h2>
        <div style={{ opacity: 0.7 }}>{week ? week.title : "..."}</div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
        <div>Selected: <b>{selected.size}</b></div>
        <button
          onClick={submitVote}
          disabled={selected.size < 1}
          style={{
            padding: "10px 14px",
            borderRadius: 12,
            border: "1px solid #eaeaea",
            background: selected.size < 1 ? "#f6f6f6" : "#111",
            color: selected.size < 1 ? "#777" : "#fff",
            cursor: selected.size < 1 ? "not-allowed" : "pointer"
          }}
        >
          VOTE
        </button>
      </div>

      <div style={{ marginTop: 12 }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search artist or title"
          style={{ width: "100%", padding: 10, borderRadius: 12, border: "1px solid #eaeaea" }}
        />
      </div>

      <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
        <button
          onClick={() => setFilter("new")}
          style={{
            padding: "8px 12px",
            borderRadius: 999,
            border: "1px solid #eaeaea",
            background: filter === "new" ? "rgba(255,63,164,0.10)" : "#fff",
            color: filter === "new" ? "#ff3fa4" : "#111",
            cursor: "pointer"
          }}
        >
          New
        </button>
        <button
          onClick={() => setFilter("all")}
          style={{
            padding: "8px 12px",
            borderRadius: 999,
            border: "1px solid #eaeaea",
            background: filter === "all" ? "rgba(255,63,164,0.10)" : "#fff",
            color: filter === "all" ? "#ff3fa4" : "#111",
            cursor: "pointer"
          }}
        >
          All
        </button>
        <div style={{ marginLeft: "auto", opacity: 0.7, padding: "8px 12px" }}>A–Z</div>
      </div>

      {error && (
        <div style={{ marginTop: 12, padding: 12, borderRadius: 12, background: "#fff4fa", border: "1px solid #ffd0e8" }}>
          {error}
        </div>
      )}

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {songs.map((s) => {
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
                cursor: "pointer"
              }}
            >
              <div style={{ width: 92, height: 92, borderRadius: 16, background: "#f2f2f2" }} />
              <div>
                <div style={{ fontSize: 18, fontWeight: 800 }}>{s.title}</div>
                <div style={{ fontSize: 14, fontWeight: 600, opacity: 0.7, marginTop: 2 }}>{s.artist}</div>
                <button
                  onClick={(e) => { e.stopPropagation(); alert("Preview подключим следующим шагом"); }}
                  style={{
                    marginTop: 10,
                    padding: "8px 12px",
                    borderRadius: 12,
                    border: "1px solid #eaeaea",
                    background: "#fff",
                    cursor: "pointer"
                  }}
                >
                  ▶ Preview
                </button>
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

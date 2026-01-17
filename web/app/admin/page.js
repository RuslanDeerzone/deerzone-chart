"use client";

import { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function downloadText(filename, text, mime = "application/json;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function toCSV(rows) {
  const esc = (v) => {
    const s = String(v ?? "");
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };

  const header = [
    "rank",
    "votes",
    "id",
    "artist",
    "title",
    "is_new",
    "weeks_in_chart",
    "source",
    "cover",
    "preview_url",
  ];

  const lines = [header.join(",")];

  rows.forEach((r, idx) => {
    lines.push(
      [
        idx + 1,
        r.votes ?? 0,
        r.id ?? "",
        r.artist ?? "",
        r.title ?? "",
        r.is_new ?? false,
        r.weeks_in_chart ?? "",
        r.source ?? "",
        r.cover ?? "",
        r.preview_url ?? "",
      ]
        .map(esc)
        .join(",")
    );
  });

  return lines.join("\n");
}

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [weekId, setWeekId] = useState(3); // у тебя сейчас только 3
  const [topN, setTopN] = useState(10);

  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [summary, setSummary] = useState(null); // { week_id, total_songs, rows }

  // подхват токена из localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem("DEERZONE_ADMIN_TOKEN") || "";
      if (saved) setToken(saved);
    } catch {}
  }, []);

  const headers = useMemo(() => {
    const h = {};
    if (token) h["X-Admin-Token"] = token;
    return h;
  }, [token]);

  async function loadSummary() {
    setErr("");
    setLoading(true);
    setSummary(null);

    try {
      const r = await fetch(`${API_BASE}/admin/weeks/${weekId}/votes/summary`, {
        method: "GET",
        headers,
        cache: "no-store",
      });

      const txt = await r.text();
      let data = null;
      try {
        data = JSON.parse(txt);
      } catch {
        throw new Error(`API вернул не-JSON: ${txt}`);
      }

      if (!r.ok) {
        throw new Error(data?.detail ? JSON.stringify(data.detail) : `HTTP ${r.status}`);
      }

      setSummary(data);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }

  function saveToken() {
    try {
      localStorage.setItem("DEERZONE_ADMIN_TOKEN", token || "");
    } catch {}
  }

  function clearToken() {
    setToken("");
    try {
      localStorage.removeItem("DEERZONE_ADMIN_TOKEN");
    } catch {}
  }

  const rows = summary?.rows || [];
  const topRows = rows.slice(0, Math.max(0, Number(topN) || 10));

  return (
    <div
      style={{
        maxWidth: 980,
        margin: "0 auto",
        padding: 18,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Inter, Arial, sans-serif",
      }}
    >
      <div style={{ height: 8, background: "#ff3fa4", borderRadius: 999 }} />

      <div style={{ marginTop: 18, fontSize: 34, fontWeight: 900 }}>
        #deerzone admin
      </div>

      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr auto auto",
            gap: 10,
            alignItems: "center",
          }}
        >
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="ADMIN TOKEN (X-Admin-Token)"
            style={{
              padding: 12,
              borderRadius: 14,
              border: "1px solid #eaeaea",
              outline: "none",
              fontSize: 14,
            }}
          />
          <button
            onClick={saveToken}
            style={{
              padding: "10px 12px",
              borderRadius: 14,
              border: "1px solid #eaeaea",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 900,
            }}
          >
            Save
          </button>
          <button
            onClick={clearToken}
            style={{
              padding: "10px 12px",
              borderRadius: 14,
              border: "1px solid #eaeaea",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 900,
              opacity: 0.8,
            }}
          >
            Clear
          </button>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ fontWeight: 900 }}>Week</div>
            <input
              value={weekId}
              onChange={(e) => setWeekId(Number(e.target.value || 0))}
              style={{
                width: 90,
                padding: 10,
                borderRadius: 12,
                border: "1px solid #eaeaea",
                outline: "none",
                fontWeight: 800,
              }}
            />
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ fontWeight: 900 }}>Top</div>
            <input
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value || 10))}
              style={{
                width: 90,
                padding: 10,
                borderRadius: 12,
                border: "1px solid #eaeaea",
                outline: "none",
                fontWeight: 800,
              }}
            />
          </div>

          <button
            onClick={loadSummary}
            disabled={!token || loading}
            style={{
              marginLeft: "auto",
              padding: "10px 14px",
              borderRadius: 14,
              border: "1px solid #eaeaea",
              background: !token || loading ? "#f5f5f5" : "#fff",
              cursor: !token || loading ? "not-allowed" : "pointer",
              fontWeight: 900,
            }}
          >
            {loading ? "Loading..." : "Load votes"}
          </button>
        </div>

        {err ? (
          <div
            style={{
              padding: 14,
              borderRadius: 16,
              border: "1px solid #ffb3d8",
              background: "rgba(255,63,164,0.08)",
              fontWeight: 900,
              whiteSpace: "pre-wrap",
            }}
          >
            {err}
          </div>
        ) : null}
      </div>

      {summary ? (
        <>
          <div style={{ marginTop: 18, display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button
              onClick={() =>
                downloadText(
                  `votes_week_${summary.week_id}.json`,
                  JSON.stringify(summary, null, 2)
                )
              }
              style={{
                padding: "10px 12px",
                borderRadius: 14,
                border: "1px solid #eaeaea",
                background: "#fff",
                cursor: "pointer",
                fontWeight: 900,
              }}
            >
              Export JSON
            </button>

            <button
              onClick={() =>
                downloadText(
                  `votes_week_${summary.week_id}.csv`,
                  toCSV(summary.rows || []),
                  "text/csv;charset=utf-8"
                )
              }
              style={{
                padding: "10px 12px",
                borderRadius: 14,
                border: "1px solid #eaeaea",
                background: "#fff",
                cursor: "pointer",
                fontWeight: 900,
              }}
            >
              Export CSV
            </button>

            <div style={{ marginLeft: "auto", fontWeight: 900, opacity: 0.7 }}>
              total songs: {summary.total_songs}
            </div>
          </div>

          <div style={{ marginTop: 16, fontSize: 22, fontWeight: 900 }}>
            Top {topN}
          </div>

          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            {topRows.map((r, idx) => (
              <div
                key={r.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "44px 70px 1fr",
                  gap: 10,
                  alignItems: "center",
                  padding: 12,
                  borderRadius: 14,
                  border: "1px solid #eaeaea",
                  background: "#fff",
                }}
              >
                <div style={{ fontWeight: 900, opacity: 0.7 }}>{idx + 1}</div>
                <div style={{ fontWeight: 900 }}>{r.votes}</div>
                <div style={{ fontWeight: 900 }}>
                  {r.artist} — <span style={{ opacity: 0.85 }}>{r.title}</span>{" "}
                  <span style={{ opacity: 0.55, fontWeight: 800 }}>
                    (id {r.id})
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 18, fontSize: 22, fontWeight: 900 }}>
            All rows
          </div>

          <div style={{ marginTop: 10, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
              <thead>
                <tr style={{ textAlign: "left" }}>
                  {["#", "votes", "id", "artist", "title", "new", "weeks", "source"].map((h) => (
                    <th
                      key={h}
                      style={{
                        padding: "10px 8px",
                        borderBottom: "2px solid #eee",
                        fontWeight: 900,
                        opacity: 0.8,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => (
                  <tr key={r.id}>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900, opacity: 0.7 }}>
                      {idx + 1}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900 }}>
                      {r.votes}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900 }}>
                      {r.id}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900 }}>
                      {r.artist}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900, opacity: 0.9 }}>
                      {r.title}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900 }}>
                      {r.is_new ? "yes" : "no"}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900 }}>
                      {r.weeks_in_chart}
                    </td>
                    <td style={{ padding: "10px 8px", borderBottom: "1px solid #f0f0f0", fontWeight: 900, opacity: 0.8 }}>
                      {r.source || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div style={{ marginTop: 18, opacity: 0.6, fontWeight: 800 }}>
          Введи токен → Save → Load votes.
        </div>
      )}
    </div>
  );
}
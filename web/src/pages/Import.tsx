import { useRef, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useImports } from "../lib/imports";
import { BackButton } from "../components/Common";

interface Started { import_id: number; playlist_id: number }
interface Status { status: "running" | "done" | "failed"; total: number; matched: number; unmatched: { title?: string; artist?: string }[]; playlist_id: number; error: string | null }

export default function Import() {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [started, setStarted] = useState<Started | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const go = async (send: () => Promise<Started>) => {
    setBusy(true); setError("");
    try { setStarted(await send()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <BackButton />
      <h1 className="display">Import a playlist</h1>
      {!started ? (
        <div className="stack">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); void go(() => api.post<Started>("/api/import", { url: url.trim(), name: name.trim() || undefined })); }}>
            <h2>From YouTube</h2>
            <label className="field"><span>Playlist link</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://music.youtube.com/playlist?list=…" inputMode="url" autoComplete="off" required /></label>
            <label className="field"><span>Name (optional)</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} /></label>
            <button className="btn primary" disabled={busy || !url.trim()}>Import playlist</button>
          </form>
          <div className="stack">
            <h2>From a CSV file</h2>
            <p className="muted">Works with Exportify exports from Spotify, or any file with title and artist columns.</p>
            <input ref={file} type="file" accept=".csv,text/csv" hidden onChange={(e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (!f) return;
              const form = new FormData(); form.append("file", f); if (name.trim()) form.append("name", name.trim());
              void go(() => api.upload<Started>("/api/import", form));
            }} />
            <button className="btn" disabled={busy} onClick={() => file.current?.click()}>Choose a CSV file</button>
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
        </div>
      ) : <Progress started={started} />}
    </div>
  );
}

function Progress({ started }: { started: Started }) {
  const live = useImports((s) => s.progress[started.import_id]);
  const q = useQuery({
    queryKey: ["import", started.import_id],
    queryFn: () => api.get<Status>(`/api/imports/${started.import_id}`),
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
  const s = q.data;
  const total = s?.total || live?.total || 0, done = s?.status === "running" ? (live?.done ?? 0) : (s?.matched ?? 0);
  return (
    <div className="stack" aria-live="polite">
      {(!s || s.status === "running") && <><p>{total ? `Matching songs: ${done} of ${total}` : "Reading the playlist…"}</p><div className="meter"><i style={{ width: total ? `${(done / total) * 100}%` : "8%" }} /></div></>}
      {s?.status === "done" && <p>{`Imported ${s.matched} of ${s.total} songs.`}</p>}
      {s?.status === "failed" && <p className="form-error" role="alert">{s.error || "The import failed."}</p>}
      {s && s.unmatched.length > 0 && s.status !== "running" && (
        <details><summary>{`${s.unmatched.length} songs weren't found`}</summary><ul className="plain">{s.unmatched.map((u, i) => <li key={i}>{[u.title, u.artist].filter(Boolean).join(" by ")}</li>)}</ul></details>
      )}
      <Link className="btn primary" to={`/playlist/${started.playlist_id}`}>Open playlist</Link>
    </div>
  );
}

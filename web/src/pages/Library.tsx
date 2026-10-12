import { useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Cover, PlaylistCover } from "../components/Cover";
import { Empty, ErrorBox, LinkRow, Spinner } from "../components/Common";
import { Icon } from "../components/Icon";
import { Sheet } from "../components/Sheet";
import type { LibraryData, PlaylistSummary } from "../types";

const CHIPS = [["playlists", "Playlists"], ["artists", "Artists"], ["albums", "Albums"]] as const;
const songs = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;

export default function Library() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [chip, setChip] = useState<(typeof CHIPS)[number][0]>("playlists");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const q = useQuery({ queryKey: ["library"], queryFn: () => api.get<LibraryData>("/api/me/library") });
  const create = useMutation({
    mutationFn: () => api.post<PlaylistSummary>("/api/playlists", { name: name.trim() }),
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ["library"] }); void qc.invalidateQueries({ queryKey: ["playlists"] }); setCreating(false); setName(""); nav(`/playlist/${p.id}`); },
  });
  const d = q.data;

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="display">Your library</h1>
        <button className="icon-btn" onClick={() => setCreating(true)} aria-label="New playlist"><Icon name="plus" /></button>
      </header>
      <div className="chips" role="tablist" aria-label="Library">
        {CHIPS.map(([id, label]) => <button key={id} role="tab" aria-selected={chip === id} className={`chip ${chip === id ? "on" : ""}`} onClick={() => setChip(id)}>{label}</button>)}
      </div>
      {q.isPending && <Spinner />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {d && chip === "playlists" && (
        <>
          <LinkRow to="/liked" art={<div className="cover liked-cover"><Icon name="heart-fill" size={26} /></div>} title="Liked Songs" sub={songs(d.liked_count)} />
          {d.playlists.map((p) => <LinkRow key={p.id} to={`/playlist/${p.id}`} art={<PlaylistCover cover={p.cover_url} collage={p.collage} />} title={p.name} sub={songs(p.track_count)} />)}
          <LinkRow to="/import" art={<div className="cover liked-cover plain"><Icon name="upload" size={24} /></div>} title="Import a playlist" sub="From YouTube or a CSV file" />
        </>
      )}
      {d && chip === "artists" && (d.artists.length
        ? d.artists.map((a) => <LinkRow key={a.id} to={`/artist/${a.id}`} art={<Cover src={a.art_url} round />} title={a.name} sub="Artist" round />)
        : <Empty title="No followed artists">Follow an artist from their page and they will show up here.</Empty>)}
      {d && chip === "albums" && (d.albums.length
        ? d.albums.map((a) => <LinkRow key={a.id} to={`/album/${a.id}`} art={<Cover src={a.art_url} />} title={a.title} sub={a.artist?.name} />)
        : <Empty title="No saved albums">Save an album from its page and it will show up here.</Empty>)}

      <Sheet open={creating} onClose={() => setCreating(false)} title="New playlist">
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Playlist name" maxLength={100} autoFocus aria-label="Playlist name" />
          <button className="btn primary" disabled={!name.trim() || create.isPending}>Create</button>
        </form>
        {create.isError && <p className="form-error pad" role="alert">{(create.error as Error).message}</p>}
      </Sheet>
    </div>
  );
}

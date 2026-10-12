import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { playItems } from "../player/engine";
import { BackButton, Empty, ErrorBox, Spinner } from "../components/Common";
import { PlaylistCover } from "../components/Cover";
import { Hero, PlayButtons } from "../components/Hero";
import { Icon, type IconName } from "../components/Icon";
import { Sheet } from "../components/Sheet";
import { TrackRow } from "../components/TrackRow";
import type { PlaylistDetail } from "../types";

type Mode = "closed" | "menu" | "edit" | "delete";

export default function Playlist() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("closed");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const q = useQuery({ queryKey: ["playlist", id], queryFn: () => api.get<PlaylistDetail>(`/api/playlists/${id}`) });
  const p = q.data;

  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["playlist", id] }), qc.invalidateQueries({ queryKey: ["playlists"] }), qc.invalidateQueries({ queryKey: ["library"] })]);
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    try { await fn(); await refresh(); if (done) toast(done); setMode("closed"); } catch (e) { toast((e as Error).message, "error"); }
  };
  const open = (m: Mode) => { if (p) { setName(p.name); setDesc(p.description); } setMode(m); };

  if (q.isPending) return <div className="page"><BackButton /><Spinner /></div>;
  if (q.isError) return <div className="page"><BackButton /><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  const tracks = p!.items.map((i) => i.track);
  const ctx = { type: "playlist", id: String(p!.id), name: p!.name } as const;
  const Item = ({ icon, children, onClick, danger }: { icon: IconName; children: string; onClick: () => void; danger?: boolean }) => (
    <button className={`action ${danger ? "danger" : ""}`} onClick={onClick}><Icon name={icon} />{children}</button>
  );

  return (
    <div className="page">
      <BackButton />
      <Hero cover={<PlaylistCover cover={p!.cover_url} collage={p!.collage} />} title={p!.name}
        meta={`${p!.track_count} ${p!.track_count === 1 ? "song" : "songs"}`}
        actions={<>
          <PlayButtons disabled={!tracks.length} onPlay={() => void playItems(ctx, tracks, 0)} onShuffle={() => void playItems(ctx, tracks, Math.floor(Math.random() * tracks.length), { shuffle: true })} />
          <button className="icon-btn" onClick={() => open("menu")} aria-label="Playlist options"><Icon name="more" /></button>
        </>}>
        {p!.description && <p className="hero-desc">{p!.description}</p>}
      </Hero>
      {!tracks.length && <Empty title="This playlist is empty">Open the menu on any song and choose Add to playlist.</Empty>}
      {p!.items.map((it, i) => (
        <TrackRow key={it.item_id} item={it.track} onPlay={() => void playItems(ctx, tracks, i)}
          playlist={{ id: p!.id, itemId: it.item_id, firstItemId: p!.items[0].item_id, index: i, count: p!.items.length }} />
      ))}

      <input ref={file} type="file" accept="image/*" hidden onChange={(e) => {
        const f = e.target.files?.[0]; e.target.value = "";
        if (!f) return;
        const form = new FormData(); form.append("file", f);
        void run(() => api.upload(`/api/playlists/${p!.id}/cover`, form, "PUT"), "Cover updated");
      }} />
      <Sheet open={mode === "menu"} onClose={() => setMode("closed")} title={p!.name}>
        <div className="actions">
          <Item icon="edit" onClick={() => open("edit")}>Edit name and description</Item>
          <Item icon="pin" onClick={() => void run(() => api.patch(`/api/playlists/${p!.id}`, { pinned_to_sidebar: !p!.pinned_to_sidebar }), p!.pinned_to_sidebar ? "Unpinned" : "Pinned to the top of your library")}>{p!.pinned_to_sidebar ? "Unpin from the top" : "Pin to the top"}</Item>
          <Item icon="image" onClick={() => file.current?.click()}>Change cover</Item>
          {p!.cover_url && <Item icon="close" onClick={() => void run(() => api.del(`/api/playlists/${p!.id}/cover`), "Cover removed")}>Remove cover</Item>}
          <Item icon="trash" danger onClick={() => setMode("delete")}>Delete playlist</Item>
        </div>
      </Sheet>
      <Sheet open={mode === "edit"} onClose={() => setMode("closed")} title="Edit playlist">
        <form className="stack pad" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void run(() => api.patch(`/api/playlists/${p!.id}`, { name: name.trim(), description: desc.trim() }), "Saved"); }}>
          <label className="field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} autoFocus /></label>
          <label className="field"><span>Description</span><textarea value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} rows={3} /></label>
          <button className="btn primary" disabled={!name.trim()}>Save</button>
        </form>
      </Sheet>
      <Sheet open={mode === "delete"} onClose={() => setMode("closed")} title="Delete this playlist?">
        <div className="stack pad">
          <p>{`“${p!.name}” will be deleted. The songs stay in your library.`}</p>
          <button className="btn danger" onClick={() => void run(async () => { await api.del(`/api/playlists/${p!.id}`); nav("/library", { replace: true }); }, "Playlist deleted")}>Delete playlist</button>
          <button className="btn" onClick={() => setMode("closed")}>Keep it</button>
        </div>
      </Sheet>
    </div>
  );
}

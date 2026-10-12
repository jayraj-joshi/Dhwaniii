import { useState } from "react";
import { useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { names } from "../lib/format";
import { toast } from "../lib/toast";
import { downloadToDevice, ensureTracks, toggleLike, useTrack, useTracks } from "../lib/tracks";
import { closeMenu, closePicker, openPicker, setQueueOpen, useUi } from "../lib/ui";
import { isTrack, jump, queueItems, removeQueued, usePlayer, type PlayItem } from "../player/engine";
import * as Q from "../player/queue";
import type { PlaylistSummary, Track } from "../types";
import { Cover, PlaylistCover } from "./Cover";
import { Icon, type IconName } from "./Icon";
import { Sheet } from "./Sheet";
import { Bars } from "./TrackRow";
import { useNowPlaying } from "../player/hooks";

const asTrack = async (item: PlayItem): Promise<Track> => (isTrack(item) ? item : (await ensureTracks([item]))[0]);

function Action({ icon, children, onClick, danger }: { icon: IconName; children: string; onClick: () => void; danger?: boolean }) {
  return <button className={`action ${danger ? "danger" : ""}`} onClick={onClick}><Icon name={icon} />{children}</button>;
}

function TrackMenu() {
  const target = useUi((s) => s.menu);
  const nav = useNavigate();
  const qc = useQueryClient();
  const item = target?.item;
  const likedOverride = useTracks((s) => (item && isTrack(item) ? s.liked[item.id] : undefined));
  const go = (to: string) => { closeMenu(); nav(to); };
  const liked = item && isTrack(item) ? (likedOverride ?? item.liked) : false;
  const pl = target?.playlist;

  const artistLinks = !item ? [] : isTrack(item)
    ? item.artists.slice(0, 2).map((a) => ({ name: a.name, to: `/artist/${a.id}` }))
    : item.artists.filter((a) => a.ytm_channel_id).slice(0, 2).map((a) => ({ name: a.name, to: `/artist/ytm:${a.ytm_channel_id}` }));
  const albumLink = !item ? null : isTrack(item)
    ? (item.album ? `/album/${item.album.id}` : null)
    : (item.album?.ytm_browse_id ? `/album/ytm:${item.album.ytm_browse_id}` : null);

  const playlistCall = async (fn: () => Promise<unknown>, done: string) => {
    try { await fn(); await qc.invalidateQueries({ queryKey: ["playlist", pl!.id] }); toast(done); } catch (e) { toast((e as Error).message, "error"); }
    closeMenu();
  };

  return (
    <Sheet open={!!target} onClose={closeMenu}>
      {item && (
        <>
          <div className="menu-head">
            <Cover src={isTrack(item) ? item.art_url : item.thumb_url} />
            <div><div className="row-title">{item.title}</div><div className="row-sub">{isTrack(item) ? names(item.artists) : item.artists.map((a) => a.name).join(", ")}</div></div>
          </div>
          <div className="actions">
            <Action icon="play" onClick={() => { void queueItems([item], "next"); closeMenu(); }}>Play next</Action>
            <Action icon="queue" onClick={() => { void queueItems([item], "end"); closeMenu(); }}>Add to queue</Action>
            <Action icon={liked ? "heart-fill" : "heart"} onClick={() => { closeMenu(); void asTrack(item).then((t) => toggleLike({ ...t, liked })).catch((e) => toast(e.message, "error")); }}>{liked ? "Remove from Liked Songs" : "Add to Liked Songs"}</Action>
            <Action icon="plus" onClick={() => openPicker(item)}>Add to playlist</Action>
            <Action icon="download" onClick={() => { closeMenu(); void asTrack(item).then(downloadToDevice).catch((e) => toast(e.message, "error")); }}>Download to device</Action>
            {pl && (
              <>
                {pl.index > 0 && <Action icon="up" onClick={() => void playlistCall(() => api.patch(`/api/playlists/${pl.id}/tracks`, { item_id: pl.itemId, before_item_id: pl.firstItemId }), "Moved to top")}>Move to top</Action>}
                {pl.index < pl.count - 1 && <Action icon="down" onClick={() => void playlistCall(() => api.patch(`/api/playlists/${pl.id}/tracks`, { item_id: pl.itemId, before_item_id: null }), "Moved to bottom")}>Move to bottom</Action>}
                <Action icon="trash" danger onClick={() => void playlistCall(() => api.del(`/api/playlists/${pl.id}/tracks`, { item_ids: [pl.itemId] }), "Removed from playlist")}>Remove from this playlist</Action>
              </>
            )}
            {artistLinks.map((a) => <Action key={a.to} icon="user" onClick={() => go(a.to)}>{`Go to ${a.name}`}</Action>)}
            {albumLink && <Action icon="library" onClick={() => go(albumLink)}>Go to album</Action>}
          </div>
        </>
      )}
    </Sheet>
  );
}

function PlaylistPicker() {
  const item = useUi((s) => s.picker);
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const lists = useQuery({ queryKey: ["playlists"], queryFn: () => api.get<PlaylistSummary[]>("/api/playlists"), enabled: !!item });

  const add = async (playlistId: number, label: string) => {
    try {
      const t = await asTrack(item!);
      await api.post(`/api/playlists/${playlistId}/tracks`, { track_ids: [t.id], at: "end" });
      toast(`Added to ${label}`);
      void qc.invalidateQueries({ queryKey: ["playlists"] });
      void qc.invalidateQueries({ queryKey: ["library"] });
      void qc.invalidateQueries({ queryKey: ["playlist", playlistId] });
      closePicker();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const p = await api.post<PlaylistSummary>("/api/playlists", { name: name.trim() });
      setName("");
      await add(p.id, p.name);
    } catch (err) { toast((err as Error).message, "error"); }
  };

  return (
    <Sheet open={!!item} onClose={closePicker} title="Add to playlist">
      <form className="inline-form" onSubmit={create}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New playlist name" maxLength={100} aria-label="New playlist name" />
        <button className="btn primary" disabled={!name.trim()}>Create</button>
      </form>
      <div className="actions">
        {lists.data?.map((p) => (
          <button key={p.id} className="action pick" onClick={() => void add(p.id, p.name)}>
            <PlaylistCover cover={p.cover_url} collage={p.collage} className="mini-cover" />
            <span><span className="row-title">{p.name}</span><span className="row-sub">{p.track_count} {p.track_count === 1 ? "song" : "songs"}</span></span>
          </button>
        ))}
        {lists.data?.length === 0 && <p className="muted pad">You have no playlists yet. Name one above.</p>}
      </div>
    </Sheet>
  );
}

function QueueRow({ id, onJump, onRemove }: { id: number; onJump: () => void; onRemove?: () => void }) {
  const t = useTrack(id);
  return (
    <div className="row">
      <button className="row-main" onClick={onJump} disabled={!t}>
        <div className="row-art"><Cover src={t?.art_url} /></div>
        <span className="row-text"><span className="row-title">{t?.title ?? "Loading…"}</span><span className="row-sub">{t ? names(t.artists) : ""}</span></span>
      </button>
      {onRemove && <button className="icon-btn" aria-label={`Remove ${t?.title ?? "track"} from the queue`} onClick={onRemove}><Icon name="close" /></button>}
    </div>
  );
}

const SHOWN = 40;

function QueueSheet() {
  const open = useUi((s) => s.queue);
  const s = usePlayer((p) => p.s);
  const finding = usePlayer((p) => p.finding);
  const { track, playing } = useNowPlaying();
  const v = Q.view(s);
  return (
    <Sheet open={open} onClose={() => setQueueOpen(false)} title="Queue">
      {track ? (
        <div className="queue">
          <h3>Now playing</h3>
          <div className="row active"><div className="row-art"><Cover src={track.art_url} /></div><span className="row-text"><span className="row-title"><Bars playing={playing} />{track.title}</span><span className="row-sub">{names(track.artists)}</span></span></div>
          {v.user.length > 0 && <h3>Next in queue</h3>}
          {v.user.map((id, i) => <QueueRow key={`u${i}-${id}`} id={id} onJump={() => jump("user", i)} onRemove={() => removeQueued("user", i)} />)}
          {v.context.length > 0 && <h3>{s.context?.type === "radio" ? "Next up" : `Next from ${s.context?.name ?? "this list"}`}</h3>}
          {v.context.slice(0, SHOWN).map((c) => <QueueRow key={`c${c.orderIndex}`} id={c.id} onJump={() => jump("context", c.orderIndex)} onRemove={() => removeQueued("context", c.orderIndex)} />)}
          {v.context.length > SHOWN && <p className="muted pad">{`and ${v.context.length - SHOWN} more`}</p>}
          {finding && <p className="muted pad">Finding more songs…</p>}
          {!v.user.length && !v.context.length && !finding && <p className="muted pad">Nothing is queued after this song.</p>}
        </div>
      ) : <p className="muted pad">Nothing is playing. Pick a song and it will show up here.</p>}
    </Sheet>
  );
}

export function Sheets() {
  return <><TrackMenu /><PlaylistPicker /><QueueSheet /></>;
}

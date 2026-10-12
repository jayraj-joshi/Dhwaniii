import { useState } from "react";
import { Link, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { playItems } from "../player/engine";
import { BackButton, ErrorBox, Spinner } from "../components/Common";
import { Cover } from "../components/Cover";
import { Hero, PlayButtons } from "../components/Hero";
import { Icon } from "../components/Icon";
import { TrackRow } from "../components/TrackRow";
import type { AlbumPage } from "../types";

export default function Album() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["album", id], queryFn: () => api.get<AlbumPage>(`/api/albums/${id}`) });
  const [saved, setSaved] = useState<boolean | null>(null);
  if (q.isPending) return <div className="page"><BackButton /><Spinner /></div>;
  if (q.isError) return <div className="page"><BackButton /><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  const a = q.data;
  const isSaved = saved ?? a.saved;
  const ctx = { type: "album", id: String(a.id), name: a.title } as const;
  const toggleSave = async () => {
    setSaved(!isSaved);
    try {
      await (isSaved ? api.del(`/api/me/albums/${a.id}`) : api.put(`/api/me/albums/${a.id}`));
      void qc.invalidateQueries({ queryKey: ["library"] });
      toast(isSaved ? "Removed from your library" : "Saved to your library");
    } catch (e) { setSaved(isSaved); toast((e as Error).message, "error"); }
  };

  return (
    <div className="page">
      <BackButton />
      <Hero cover={<Cover src={a.art_url} />} title={a.title}
        meta={[a.year, `${a.tracks.length} ${a.tracks.length === 1 ? "song" : "songs"}`].filter(Boolean).join(", ")}
        actions={<>
          <PlayButtons disabled={!a.tracks.length} onPlay={() => void playItems(ctx, a.tracks, 0)} onShuffle={() => void playItems(ctx, a.tracks, Math.floor(Math.random() * a.tracks.length), { shuffle: true })} />
          <button className={`icon-btn ${isSaved ? "on" : ""}`} onClick={() => void toggleSave()} aria-pressed={isSaved} aria-label={isSaved ? "Remove album from your library" : "Save album to your library"}><Icon name={isSaved ? "heart-fill" : "heart"} /></button>
        </>}>
        {a.artist && <p className="hero-artist"><Link to={`/artist/${a.artist.id}`}>{a.artist.name}</Link></p>}
      </Hero>
      {a.tracks.map((t, i) => <TrackRow key={t.video_id} item={t} num={t.track_number ?? i + 1} onPlay={() => void playItems(ctx, a.tracks, i)} />)}
    </div>
  );
}

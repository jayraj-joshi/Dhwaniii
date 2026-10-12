import { useState } from "react";
import { useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { playItems } from "../player/engine";
import { BackButton, ErrorBox, Section, Shelf, ShelfItem, Spinner } from "../components/Common";
import { Cover } from "../components/Cover";
import { Icon } from "../components/Icon";
import { TrackRow } from "../components/TrackRow";
import type { ArtistPage } from "../types";

export default function Artist() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["artist", id], queryFn: () => api.get<ArtistPage>(`/api/artists/${id}`) });
  const [follow, setFollow] = useState<boolean | null>(null);
  const [more, setMore] = useState(false);
  const [full, setFull] = useState(false);
  if (q.isPending) return <div className="page"><BackButton /><Spinner /></div>;
  if (q.isError) return <div className="page"><BackButton /><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  const a = q.data;
  const followed = follow ?? a.followed;
  const ctx = { type: "artist", id: String(a.id), name: a.name } as const;
  const popular = more ? a.popular : a.popular.slice(0, 5);
  const toggleFollow = async () => {
    setFollow(!followed);
    try {
      await (followed ? api.del(`/api/me/follows/${a.id}`) : api.put(`/api/me/follows/${a.id}`));
      void qc.invalidateQueries({ queryKey: ["library"] });
    } catch (e) { setFollow(followed); toast((e as Error).message, "error"); }
  };

  return (
    <div className="page artist">
      <BackButton />
      <header className="artist-hero">
        <Cover src={a.art_url} className="artist-art" />
        <h1 className="display">{a.name}</h1>
      </header>
      {a.plays_30d > 0 && <p className="muted">{`You played ${a.name} ${a.plays_30d} ${a.plays_30d === 1 ? "time" : "times"} in the last 30 days.`}</p>}
      <div className="row-actions">
        <button className="btn primary" disabled={!a.popular.length} onClick={() => void playItems(ctx, a.popular, 0)}><Icon name="play" size={20} />Play</button>
        <button className={`btn ${followed ? "on" : ""}`} onClick={() => void toggleFollow()} aria-pressed={followed}>{followed ? "Following" : "Follow"}</button>
      </div>
      {a.popular.length > 0 && (
        <Section title="Popular">
          {popular.map((t, i) => <TrackRow key={t.video_id} item={t} num={i + 1} onPlay={() => void playItems(ctx, a.popular, i)} />)}
          {a.popular.length > 5 && <button className="link-btn pad" onClick={() => setMore(!more)}>{more ? "Show fewer" : "Show more"}</button>}
        </Section>
      )}
      {a.discography.length > 0 && (
        <Section title="Discography">
          <Shelf>{a.discography.map((d) => (
            <ShelfItem key={d.ytm_browse_id ?? d.id} to={`/album/${d.id ?? `ytm:${d.ytm_browse_id}`}`} art={d.id ? `/api/art/album/${d.id}` : d.thumb_url} title={d.title}
              sub={[d.year, d.type === "single" ? "Single" : ""].filter(Boolean).join(", ")} />
          ))}</Shelf>
        </Section>
      )}
      {a.fans_also_like.length > 0 && (
        <Section title="Fans also like">
          <Shelf>{a.fans_also_like.map((f) => <ShelfItem key={f.ytm_channel_id} to={`/artist/${f.id ?? `ytm:${f.ytm_channel_id}`}`} art={f.thumb_url} title={f.name} round />)}</Shelf>
        </Section>
      )}
      {a.bio && (
        <Section title="About">
          <p className={`bio ${full ? "" : "clamp"}`}>{a.bio}</p>
          <button className="link-btn" onClick={() => setFull(!full)}>{full ? "Show less" : "Read more"}</button>
        </Section>
      )}
    </div>
  );
}

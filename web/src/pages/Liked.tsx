import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { playItems } from "../player/engine";
import { BackButton, Empty, ErrorBox, Spinner } from "../components/Common";
import { Hero, PlayButtons } from "../components/Hero";
import { Icon } from "../components/Icon";
import { TrackRow } from "../components/TrackRow";
import { remember } from "../lib/tracks";
import type { Track } from "../types";

const PAGE = 100;

export default function Liked() {
  const q = useInfiniteQuery({
    queryKey: ["liked"], initialPageParam: 0,
    queryFn: async ({ pageParam }) => { const t = await api.get<Track[]>(`/api/me/likes?offset=${pageParam}&limit=${PAGE}`); remember(t); return t; },
    getNextPageParam: (last, all) => (last.length === PAGE ? all.length * PAGE : undefined),
  });
  const tracks = q.data?.pages.flat() ?? [];
  const ctx = { type: "liked", id: "liked", name: "Liked Songs" } as const;

  return (
    <div className="page">
      <BackButton />
      <Hero cover={<div className="cover liked-cover big"><Icon name="heart-fill" size={64} /></div>} title="Liked Songs"
        meta={q.data ? `${tracks.length}${q.hasNextPage ? "+" : ""} ${tracks.length === 1 ? "song" : "songs"}` : undefined}
        actions={<PlayButtons disabled={!tracks.length} onPlay={() => void playItems(ctx, tracks, 0)} onShuffle={() => void playItems(ctx, tracks, Math.floor(Math.random() * tracks.length), { shuffle: true })} />} />
      {q.isPending && <Spinner />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {q.data && !tracks.length && <Empty title="No liked songs yet">Tap the heart on a song to keep it here. Liked songs are never deleted to make room.</Empty>}
      {tracks.map((t, i) => <TrackRow key={t.id} item={t} onPlay={() => void playItems(ctx, tracks, i)} />)}
      {q.hasNextPage && <div className="center-actions"><button className="btn" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>Show more</button></div>}
    </div>
  );
}

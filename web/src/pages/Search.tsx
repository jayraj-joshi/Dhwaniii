import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { playSong } from "../player/engine";
import { Cover, PlaylistCover } from "../components/Cover";
import { Empty, ErrorBox, LinkRow, Section, Shelf, ShelfItem, Spinner } from "../components/Common";
import { Icon } from "../components/Icon";
import { TrackRow } from "../components/TrackRow";
import type { RemoteTrack, SearchResult, Track } from "../types";

const TABS = [["all", "All"], ["songs", "Songs"], ["artists", "Artists"], ["albums", "Albums"], ["playlists", "Playlists"]] as const;
type Tab = (typeof TABS)[number][0];

function useDebounced<T>(v: T, ms: number) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

interface Suggestion { kind: "track" | "artist" | "album"; id: number; label: string; sublabel: string }

export default function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tab = (TABS.find((t) => t[0] === params.get("tab"))?.[0] ?? "all") as Tab;
  const [text, setText] = useState(q);
  const [focused, setFocused] = useState(false);
  const [deep, setDeep] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const recorded = useRef("");
  useEffect(() => setText(q), [q]);
  useEffect(() => setDeep(false), [q]);

  const typed = useDebounced(text.trim(), 160);
  const suggesting = focused && typed.length >= 2 && text.trim() !== q;
  const suggest = useQuery({
    queryKey: ["suggest", typed], enabled: suggesting,
    queryFn: () => api.get<Suggestion[]>(`/api/suggest?q=${encodeURIComponent(typed)}`), placeholderData: keepPreviousData,
  });
  const completions = useQuery({ // YouTube Music's autocomplete: slower, so it fills in under the library matches
    queryKey: ["suggest-queries", typed], enabled: suggesting, staleTime: Infinity,
    queryFn: () => api.get<string[]>(`/api/suggest/queries?q=${encodeURIComponent(typed)}`), placeholderData: keepPreviousData,
  });
  const res = useQuery({
    queryKey: ["search", q, tab, deep], enabled: !!q.trim(), placeholderData: keepPreviousData,
    queryFn: () => api.get<SearchResult>(`/api/search?q=${encodeURIComponent(q)}&type=${tab}${deep ? "&remote=always" : ""}`),
  });
  const recents = useQuery({ queryKey: ["searches"], queryFn: () => api.get<{ query: string; art_url: string | null }[]>("/api/me/searches"), enabled: !q });

  const run = (query: string, nextTab: Tab = tab) => {
    const t = query.trim();
    if (!t) return;
    setParams(nextTab === "all" ? { q: t } : { q: t, tab: nextTab });
    setFocused(false);
    (document.activeElement as HTMLElement | null)?.blur();
  };
  /** A search is worth remembering once the user does something with a result. */
  const remember = (videoId: string) => {
    if (!q || recorded.current === q) return;
    recorded.current = q;
    api.post("/api/me/searches", { query: q, video_id: videoId }).then(() => qc.invalidateQueries({ queryKey: ["searches"] })).catch(() => {});
  };
  const play = (t: Track | RemoteTrack) => { remember(t.video_id); void playSong(t); };
  const local = suggest.data ?? [];
  const titles = new Set(local.filter((s) => s.kind === "track").map((s) => s.label.toLowerCase()));
  const queries = (completions.data ?? []).filter((c) => !titles.has(c.toLowerCase())); // a library song already covers it
  const showSuggestions = focused && text.trim().length >= 2 && text.trim() !== q && local.length + queries.length > 0;
  const d = res.data;

  const songsBlock = (list: Track[], limit: number) => list.slice(0, limit).map((t) => <TrackRow key={t.id} item={t} onPlay={() => play(t)} />);
  const songList = d && tab === "all" && d.top?.kind === "song" ? d.songs.slice(1) : (d?.songs ?? []); // the top result is already the first song
  const youtubeBlock = (d?.youtube.length ?? 0) > 0 && (
    <Section title="From YouTube">{d!.youtube.slice(0, tab === "all" ? 6 : 20).map((t) => <TrackRow key={t.video_id} item={t} onPlay={() => play(t)} />)}</Section>
  );
  const moreYoutube = !deep && d && (tab === "all" || tab === "songs") && d.youtube.length === 0 && d.songs.length > 0 && (
    <div className="center-actions"><button className="btn" onClick={() => setDeep(true)}>Look on YouTube too</button></div>
  );
  const artistLink = (id: number | null, ch?: string) => `/artist/${id ?? `ytm:${ch}`}`;
  const albumLink = (id: number | null, b?: string) => `/album/${id ?? `ytm:${b}`}`;

  return (
    <div className="page">
      <h1 className="display">Search</h1>
      <form role="search" className="searchbox" onSubmit={(e) => { e.preventDefault(); run(text); }}>
        <Icon name="search" size={22} />
        <input id="search-input" type="search" value={text} placeholder="Songs, artists, albums" autoComplete="off" autoCorrect="off" spellCheck={false} enterKeyHint="search"
          aria-label="Search" onChange={(e) => setText(e.target.value)} onFocus={() => setFocused(true)} onBlur={() => setTimeout(() => setFocused(false), 150)} />
        {text && <button type="button" className="icon-btn" aria-label="Clear search" onMouseDown={(e) => e.preventDefault()} onClick={() => { setText(""); setParams({}); document.getElementById("search-input")?.focus(); }}><Icon name="close" size={20} /></button>}
      </form>

      {showSuggestions && (
        <ul className="suggest" aria-label="Suggestions">
          {local.map((s) => (
            <li key={`${s.kind}${s.id}`}>
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => (s.kind === "track" ? run(s.label) : nav(`/${s.kind}/${s.id}`))}>
                <Icon name={s.kind === "artist" ? "user" : s.kind === "album" ? "library" : "search"} size={20} />
                <span><span className="row-title">{s.label}</span><span className="row-sub">{s.sublabel}</span></span>
              </button>
            </li>
          ))}
          {queries.map((c) => (
            <li key={`q:${c}`}>
              <button onMouseDown={(e) => e.preventDefault()} onClick={() => run(c)}>
                <Icon name="search" size={20} />
                <span><span className="row-title">{c}</span></span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!q && (
        recents.data?.length ? (
          <Section title="Recent searches" action={<button className="link-btn" onClick={() => api.del("/api/me/searches").then(() => qc.invalidateQueries({ queryKey: ["searches"] })).catch((e) => toast(e.message, "error"))}>Clear</button>}>
            <ul className="recents">{recents.data.map((r) => (
              <li key={r.query}>
                <button onClick={() => run(r.query)}>{r.art_url ? <Cover src={r.art_url} className="recent-art" /> : <Icon name="search" size={20} />}{r.query}</button>
                <button className="icon-btn" aria-label={`Remove ${r.query}`} onClick={() => api.del(`/api/me/searches?query=${encodeURIComponent(r.query)}`).then(() => qc.invalidateQueries({ queryKey: ["searches"] })).catch(() => {})}><Icon name="close" size={18} /></button>
              </li>
            ))}</ul>
          </Section>
        ) : <Empty title="Find something to play">A song you've played before is found instantly. Anything else is found on YouTube and saved for next time.</Empty>
      )}

      {q && (
        <>
          <div className="chips" role="tablist" aria-label="Result type">
            {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={`chip ${tab === id ? "on" : ""}`} onClick={() => run(q, id)}>{label}</button>)}
          </div>
          {res.isPending && <Spinner />}
          {res.isError && <ErrorBox error={res.error} retry={() => res.refetch()} />}
          {d && (
            <div className={res.isPlaceholderData ? "stale" : ""}>
              {tab === "all" && d.top && (
                <Section title="Top result">
                  {d.top.kind === "song"
                    ? <TrackRow item={d.top.item} onPlay={() => play(d.top!.item as Track)} />
                    : <LinkRow to={`/artist/${d.top.item.id}`} art={<Cover src={d.top.item.art_url} round />} title={d.top.item.name} sub="Artist" round />}
                </Section>
              )}
              {(tab === "all" || tab === "songs") && songList.length > 0 && <Section title="Songs">{songsBlock(songList, tab === "all" ? 5 : 30)}</Section>}
              {(tab === "all" || tab === "songs") && youtubeBlock}
              {moreYoutube}
              {(tab === "all" || tab === "artists") && d.artists.length > 0 && (
                <Section title="Artists">
                  {tab === "all"
                    ? <Shelf>{d.artists.slice(0, 10).map((a) => <ShelfItem key={a.id} to={`/artist/${a.id}`} art={a.art_url} title={a.name} round />)}</Shelf>
                    : d.artists.map((a) => <LinkRow key={a.id} to={`/artist/${a.id}`} art={<Cover src={a.art_url} round />} title={a.name} sub="Artist" round />)}
                </Section>
              )}
              {tab === "artists" && d.youtube_artists.length > 0 && (
                <Section title="From YouTube Music">{d.youtube_artists.map((a) => <LinkRow key={a.ytm_channel_id} to={artistLink(a.id, a.ytm_channel_id)} art={<Cover src={a.thumb_url} round />} title={a.name} sub="Artist" round />)}</Section>
              )}
              {(tab === "all" || tab === "albums") && d.albums.length > 0 && (
                <Section title="Albums">
                  {tab === "all"
                    ? <Shelf>{d.albums.slice(0, 10).map((a) => <ShelfItem key={a.id} to={`/album/${a.id}`} art={a.art_url} title={a.title} sub={a.artist?.name} />)}</Shelf>
                    : d.albums.map((a) => <LinkRow key={a.id} to={`/album/${a.id}`} art={<Cover src={a.art_url} />} title={a.title} sub={[a.artist?.name, a.year].filter(Boolean).join(", ")} />)}
                </Section>
              )}
              {tab === "albums" && d.youtube_albums.length > 0 && (
                <Section title="From YouTube Music">{d.youtube_albums.map((a) => <LinkRow key={a.ytm_browse_id} to={albumLink(a.id, a.ytm_browse_id)} art={<Cover src={a.thumb_url} />} title={a.title} sub={[a.artist, a.year].filter(Boolean).join(", ")} />)}</Section>
              )}
              {(tab === "all" || tab === "playlists") && d.playlists.length > 0 && (
                <Section title="Your playlists">
                  {d.playlists.map((p) => <LinkRow key={p.id} to={`/playlist/${p.id}`} art={<PlaylistCover cover={p.cover_url} collage={p.collage} />} title={p.name} sub={`${p.track_count} ${p.track_count === 1 ? "song" : "songs"}`} />)}
                </Section>
              )}
              {!d.songs.length && !d.youtube.length && !d.artists.length && !d.albums.length && !d.playlists.length && !d.youtube_artists.length && !d.youtube_albums.length && !res.isFetching && (
                <Empty title={`Nothing found for “${q}”`}>Check the spelling, or try the artist's name with the song title.{" "}{!deep && tab !== "playlists" && <button className="link-btn" onClick={() => setDeep(true)}>Search YouTube</button>}</Empty>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

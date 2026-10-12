import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { greeting } from "../lib/format";
import { getTrack } from "../lib/tracks";
import { playSong } from "../player/engine";
import { Avatar, Empty, ErrorBox, Section, Shelf, ShelfItem, Spinner, useMe } from "../components/Common";
import { Cover } from "../components/Cover";
import { Icon } from "../components/Icon";
import { TrackRow } from "../components/TrackRow";
import type { HomeSection, RecentContext } from "../types";

const hrefOf = (c: RecentContext) => (c.kind === "liked" ? "/liked" : ["playlist", "album", "artist"].includes(c.kind) ? `/${c.kind}/${c.id}` : null);
const playRadioSeed = (c: RecentContext) => c.id != null && void getTrack(c.id).then(playSong);

function Tile({ c }: { c: RecentContext }) {
  const to = hrefOf(c);
  const art = c.kind === "liked" ? <div className="cover liked-cover"><Icon name="heart-fill" size={22} /></div> : <Cover src={c.art_url} />;
  const body = <><div className="tile-art">{art}</div><span>{c.name}</span></>;
  return to ? <Link className="tile" to={to}>{body}</Link> : <button className="tile" onClick={() => playRadioSeed(c)}>{body}</button>;
}

export default function Home() {
  const me = useMe();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["home"], queryFn: () => api.get<{ sections: HomeSection[] }>("/api/home") });
  const sec = (id: HomeSection["id"]) => q.data?.sections.find((s) => s.id === id);
  const quick = sec("quick_access"), jump = sec("jump_back_in"), repeat = sec("on_repeat"), added = sec("recently_added");
  const fresh = q.data && !jump && !repeat && !added;

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="display">{greeting()}</h1>
        {me.data && <Link to="/settings" className="head-avatar" aria-label="Settings and profile"><Avatar profile={me.data} size={40} /></Link>}
      </header>
      {q.isPending && <Spinner />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {quick && <div className="tiles">{(quick.items as RecentContext[]).map((c) => <Tile key={c.context} c={c} />)}</div>}
      {fresh && (
        <Empty title="Your library is empty">
          Search for a song and it plays within seconds. Every song you play is saved here for next time.
        </Empty>
      )}
      {fresh && <div className="center-actions"><button className="btn primary" onClick={() => nav("/search")}>Find a song</button></div>}
      {jump && (
        <Section title={jump.title}>
          <Shelf>{(jump.items as RecentContext[]).map((c) => {
            const to = hrefOf(c);
            return <ShelfItem key={c.context} to={to ?? undefined} onClick={to ? undefined : () => playRadioSeed(c)} art={c.art_url} title={c.name} round={c.kind === "artist"} />;
          })}</Shelf>
        </Section>
      )}
      {repeat && <Section title={repeat.title}>{repeat.items.map((t) => <TrackRow key={t.id} item={t as never} onPlay={() => void playSong(t as never)} />)}</Section>}
      {added && <Section title={added.title}>{added.items.map((t) => <TrackRow key={t.id} item={t as never} onPlay={() => void playSong(t as never)} />)}</Section>}
    </div>
  );
}

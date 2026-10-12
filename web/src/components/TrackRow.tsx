import { Cover } from "./Cover";
import { Icon } from "./Icon";
import { duration, names } from "../lib/format";
import { openMenu, type MenuTarget } from "../lib/ui";
import { useTracks } from "../lib/tracks";
import { isTrack, type PlayItem } from "../player/engine";
import { useNowPlaying } from "../player/hooks";
import type { CacheState } from "../types";

/** The four download states (D14): nothing when cached, a ring while downloading, a cloud when it isn't on the server yet, a warning when it failed. */
export function DlState({ state, pct }: { state: CacheState; pct?: number }) {
  if (state === "cached") return null;
  if (state === "downloading") {
    return (
      <span className="dl" role="img" aria-label={pct != null ? `Downloading, ${Math.round(pct)}%` : "Downloading"} title="Downloading">
        <svg viewBox="0 0 20 20" width="18" height="18" className={pct == null ? "spin" : ""}>
          <circle cx="10" cy="10" r="7.500" className="dl-track" />
          <circle cx="10" cy="10" r="7.500" className="dl-bar" pathLength={100} strokeDasharray={`${pct ?? 28} 100`} transform="rotate(-90 10 10)" />
        </svg>
      </span>
    );
  }
  if (state === "failed") return <span className="dl warn" role="img" aria-label="Download failed, it still plays live" title="Download failed, it still plays live"><Icon name="warning" size={18} /></span>;
  return <span className="dl" role="img" aria-label="Not downloaded yet, plays live" title="Not downloaded yet, plays live"><Icon name="cloud" size={18} /></span>;
}

interface Props { item: PlayItem; onPlay: () => void; num?: number; playlist?: MenuTarget["playlist"] }

export function TrackRow({ item, onPlay, num, playlist }: Props) {
  const track = isTrack(item) ? item : undefined;
  const cache = useTracks((s) => s.cacheByVid[item.video_id]);
  const pct = useTracks((s) => s.progress[item.video_id]);
  const now = useNowPlaying();
  const active = now.track?.video_id === item.video_id;
  const state: CacheState = cache ?? track?.cache_state ?? "remote";
  const artists = track ? names(track.artists) : item.artists.map((a) => a.name).join(", ");
  const art = track ? track.art_url : (item as { thumb_url: string | null }).thumb_url;

  return (
    <div className={`row ${active ? "active" : ""}`}>
      <button className="row-main" onClick={onPlay} aria-label={`Play ${item.title}${artists ? ` by ${artists}` : ""}`}>
        {num != null ? <span className="row-num">{num}</span> : <div className="row-art"><Cover src={art} /></div>}
        <span className="row-text">
          <span className="row-title">{active && <Bars playing={now.playing} />}{item.title}</span>
          <span className="row-sub">{artists}</span>
        </span>
      </button>
      <DlState state={state} pct={pct} />
      <span className="row-time">{duration(item.duration_ms)}</span>
      <button className="icon-btn" aria-label={`More options for ${item.title}`} onClick={() => openMenu({ item, playlist })}><Icon name="more" /></button>
    </div>
  );
}

export function Bars({ playing }: { playing: boolean }) {
  return <span className={`bars ${playing ? "on" : ""}`} aria-hidden="true"><i /><i /><i /></span>;
}

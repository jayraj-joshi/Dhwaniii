import { useEffect, useState } from "react";
import { Link } from "react-router";
import { asset } from "../lib/api";
import { duration, names } from "../lib/format";
import { toggleLike, useLive } from "../lib/tracks";
import { setFull, setQueueOpen, useUi } from "../lib/ui";
import { cycleRepeat, setVolume, skipNext, skipPrev, togglePlay, toggleShuffle, usePlayer } from "../player/engine";
import { useNowPlaying } from "../player/hooks";
import type { Track } from "../types";
import { Cover } from "./Cover";
import { Icon } from "./Icon";
import { StringBar } from "./StringBar";

/** Shuffle, previous, play/pause, next, repeat. */
function Transport({ big = false }: { big?: boolean }) {
  const { status } = useNowPlaying();
  const shuffle = usePlayer((s) => s.s.shuffle);
  const repeat = usePlayer((s) => s.s.repeat);
  const playing = status === "playing" || status === "loading";
  return (
    <div className={`transport ${big ? "big" : ""}`}>
      <button className={`icon-btn ${shuffle ? "on" : ""}`} onClick={toggleShuffle} aria-label="Shuffle" aria-pressed={shuffle}><Icon name="shuffle" /></button>
      <button className="icon-btn" onClick={skipPrev} aria-label="Previous"><Icon name="prev" size={28} /></button>
      <button className={`play-btn ${status === "loading" ? "busy" : ""}`} onClick={togglePlay} aria-label={playing ? "Pause" : "Play"}><Icon name={playing ? "pause" : "play"} size={big ? 34 : 26} /></button>
      <button className="icon-btn" onClick={skipNext} aria-label="Next"><Icon name="next" size={28} /></button>
      <button className={`icon-btn ${repeat !== "off" ? "on" : ""}`} onClick={cycleRepeat} aria-label={`Repeat: ${repeat}`}><Icon name={repeat === "one" ? "repeat-one" : "repeat"} /></button>
    </div>
  );
}

function useSubtitle(track: Track | undefined) {
  const finding = usePlayer((s) => s.finding);
  const active = usePlayer((s) => s.activeDevice);
  const remote = usePlayer((s) => s.remotePlaying);
  const devices = usePlayer((s) => s.devices);
  if (finding) return "Finding more songs…";
  const other = devices.find((d) => d.id === active);
  if (remote && other && active) return `Playing on ${other.name}`;
  return track ? names(track.artists) : "";
}

function LikeButton({ track }: { track: Track }) {
  const t = useLive(track);
  return <button className={`icon-btn like ${t.liked ? "on" : ""}`} onClick={() => toggleLike(t)} aria-pressed={t.liked} aria-label={t.liked ? "Remove from Liked Songs" : "Add to Liked Songs"}><Icon name={t.liked ? "heart-fill" : "heart"} /></button>;
}

/** Phones: sits above the tab bar. Tap it to open the full player. */
export function MiniPlayer() {
  const { track, status } = useNowPlaying();
  const ratio = usePlayer((s) => (s.durationMs ? s.positionMs / s.durationMs : 0));
  const sub = useSubtitle(track);
  if (!track) return null;
  const playing = status === "playing" || status === "loading";
  return (
    <div className="mini" role="group" aria-label="Now playing">
      <button className="mini-open" onClick={() => setFull(true)} aria-label={`Open player, ${track.title}`}>
        <Cover src={track.art_url} />
        <span className="row-text"><span className="row-title">{track.title}</span><span className="row-sub">{sub}</span></span>
      </button>
      <LikeButton track={track} />
      <button className="icon-btn" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"}><Icon name={playing ? "pause" : "play"} size={28} /></button>
      <span className="mini-line" aria-hidden="true"><i style={{ transform: `scaleX(${Math.min(1, ratio)})` }} /></span>
    </div>
  );
}

const clock = (ms: number) => duration(ms) || "0:00";

/** Wide screens: the full-width bar along the bottom. */
export function DesktopBar() {
  const { track } = useNowPlaying();
  const pos = usePlayer((s) => s.positionMs);
  const dur = usePlayer((s) => s.durationMs);
  const volume = usePlayer((s) => s.volume);
  const sub = useSubtitle(track);
  return (
    <div className="desk-bar">
      <div className="desk-now">
        {track && (
          <>
            <button className="mini-open" onClick={() => setFull(true)} aria-label="Open player"><Cover src={track.art_url} /></button>
            <div className="row-text"><div className="row-title">{track.title}</div><div className="row-sub">{sub}</div></div>
            <LikeButton track={track} />
          </>
        )}
      </div>
      <div className="desk-mid">
        <Transport />
        <div className="desk-time"><span>{clock(pos)}</span><StringBar height={30} /><span>{clock(dur)}</span></div>
      </div>
      <div className="desk-right">
        <button className="icon-btn" onClick={() => setQueueOpen(true)} aria-label="Queue"><Icon name="queue" /></button>
        <Icon name="volume" size={20} />
        <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => setVolume(Number(e.target.value))} aria-label="Volume" />
      </div>
    </div>
  );
}

/** Average colour of the artwork, to tint the full player. Needs the image to be CORS-readable; otherwise the page colour is used. */
function useArtTint(src: string | null) {
  const [tint, setTint] = useState<string | null>(null);
  useEffect(() => {
    setTint(null);
    if (!src) return;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = c.height = 8;
        const ctx = c.getContext("2d")!;
        ctx.drawImage(img, 0, 0, 8, 8);
        const d = ctx.getImageData(0, 0, 8, 8).data;
        let r = 0, g = 0, b = 0, w = 0;
        for (let i = 0; i < d.length; i += 4) {
          const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
          const weight = 0.2 + (mx - mn) / 255; // favour the colourful pixels over grey ones
          r += d[i] * weight; g += d[i + 1] * weight; b += d[i + 2] * weight; w += weight;
        }
        setTint(`rgb(${Math.round(r / w)} ${Math.round(g / w)} ${Math.round(b / w)})`);
      } catch { /* tainted canvas: keep the default background */ }
    };
    img.src = src;
  }, [src]);
  return tint;
}

function FullBody({ track }: { track: Track }) {
  const context = usePlayer((s) => s.s.context);
  const pos = usePlayer((s) => s.positionMs);
  const dur = usePlayer((s) => s.durationMs);
  const sub = useSubtitle(track);
  const tint = useArtTint(asset(track.art_url));
  const [y0, setY0] = useState<number | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="full" role="dialog" aria-modal="true" aria-label="Now playing" style={tint ? ({ "--tint": tint } as React.CSSProperties) : undefined}>
      <div className="full-top" onTouchStart={(e) => setY0(e.touches[0].clientY)} onTouchEnd={(e) => { if (y0 != null && e.changedTouches[0].clientY - y0 > 90) setFull(false); setY0(null); }}>
        <header>
          <button className="icon-btn" onClick={() => setFull(false)} aria-label="Close player" autoFocus><Icon name="down" /></button>
          <div className="full-from">{context ? <><span>Playing from</span><strong>{context.name}</strong></> : null}</div>
          <button className="icon-btn" onClick={() => setQueueOpen(true)} aria-label="Queue"><Icon name="queue" /></button>
        </header>
        <Cover src={track.art_url} className="full-art" />
      </div>
      <div className="full-info">
        <div>
          <h2 className="full-title">{track.title}</h2>
          <p className="full-artist">
            {sub.startsWith("Playing on") || sub.startsWith("Finding") ? sub : track.artists.map((a, i) => (
              <span key={a.id}>{i > 0 && ", "}<Link to={`/artist/${a.id}`} onClick={() => setFull(false)}>{a.name}</Link></span>
            ))}
          </p>
        </div>
        <LikeButton track={track} />
      </div>
      <div className="full-string">
        <StringBar height={52} />
        <div className="full-times"><span>{clock(pos)}</span><span>{clock(dur)}</span></div>
      </div>
      <Transport big />
    </div>
  );
}

export function FullPlayer() {
  const open = useUi((s) => s.full);
  const { track } = useNowPlaying();
  useEffect(() => { if (open && !track) setFull(false); }, [open, track]);
  return open && track ? <FullBody track={track} /> : null;
}


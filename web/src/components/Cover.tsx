import { useEffect, useState } from "react";
import { asset } from "../lib/api";

/** What stands in for missing artwork: the app's own rings. */
export function Rings() {
  return (
    <svg className="rings" viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="40" /><circle cx="50" cy="50" r="27" /><circle cx="50" cy="50" r="14" /><circle className="dot" cx="50" cy="50" r="4.500" />
    </svg>
  );
}

function Img({ src }: { src: string | null | undefined }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  const url = asset(src);
  if (!url || bad) return <Rings />;
  // Artwork from our own API is fetched with CORS so the service worker can cache it properly and the player can read its colours.
  return <img src={url} alt="" loading="lazy" decoding="async" crossOrigin={src!.startsWith("/") ? "anonymous" : undefined} onError={() => setBad(true)} />;
}

export function Cover({ src, className = "", round = false }: { src: string | null | undefined; className?: string; round?: boolean }) {
  return <div className={`cover ${round ? "round" : ""} ${className}`}><Img src={src} /></div>;
}

/** A playlist's own cover, else a 2×2 of its first tracks (the server sends up to four). */
export function PlaylistCover({ cover, collage, className = "" }: { cover: string | null; collage: string[]; className?: string }) {
  if (cover) return <Cover src={cover} className={className} />;
  if (collage.length < 4) return <Cover src={collage[0]} className={className} />;
  return <div className={`cover collage ${className}`}>{collage.slice(0, 4).map((u, i) => <Img key={i} src={u} />)}</div>;
}

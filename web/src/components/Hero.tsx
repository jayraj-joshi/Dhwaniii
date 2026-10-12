import type { ReactNode } from "react";
import { Icon } from "./Icon";

/** The header of a playlist, album or Liked Songs: cover, title, a line of facts, and the buttons. */
export function Hero({ cover, title, meta, children, actions }: { cover: ReactNode; title: string; meta?: ReactNode; children?: ReactNode; actions: ReactNode }) {
  return (
    <header className="hero">
      <div className="hero-cover">{cover}</div>
      <div className="hero-text">
        <h1 className="display hero-title">{title}</h1>
        {children}
        {meta && <p className="hero-meta">{meta}</p>}
      </div>
      <div className="hero-actions">{actions}</div>
    </header>
  );
}

export function PlayButtons({ onPlay, onShuffle, disabled }: { onPlay: () => void; onShuffle: () => void; disabled?: boolean }) {
  return (
    <>
      <button className="btn primary" onClick={onPlay} disabled={disabled}><Icon name="play" size={20} />Play</button>
      <button className="btn" onClick={onShuffle} disabled={disabled}><Icon name="shuffle" size={20} />Shuffle</button>
    </>
  );
}

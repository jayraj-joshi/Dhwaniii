import { useEffect, useRef } from "react";
import { getPositionMs, seek, usePlayer } from "../player/engine";

const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * The progress bar is a string. Ahead of the playhead it lies flat. Behind it, the string is held at both ends (the nut and the
 * playhead, like a finger on a fret) and vibrates while music plays. Seeking plucks it.
 */
export function StringBar({ height = 48 }: { height?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const duration = usePlayer((s) => s.durationMs);
  const status = usePlayer((s) => s.status);
  const live = useRef({ duration, playing: false, drag: null as number | null, pluck: 0 });
  live.current.duration = duration;
  live.current.playing = status === "playing";
  const kick = useRef<() => void>(() => {});

  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext("2d")!;
    let raf = 0, amp = 0, t0 = performance.now(), w = 0, h = 0;
    let colors = { accent: "#1db954", line: "#999", ink: "#000" };

    const readColors = () => {
      const cs = getComputedStyle(el);
      colors = { accent: cs.getPropertyValue("--accent-text").trim(), line: cs.getPropertyValue("--line-strong").trim(), ink: cs.getPropertyValue("--ink").trim() };
    };
    const fit = () => {
      const r = el.getBoundingClientRect(), dpr = devicePixelRatio || 1;
      w = r.width; h = r.height;
      el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      readColors();
    };

    const draw = (now: number) => {
      const L = live.current, p = L.drag ?? (L.duration ? Math.min(1, getPositionMs() / L.duration) : 0);
      const pad = 8, span = w - pad * 2, xp = pad + span * p, mid = h / 2;
      const still = reduced();
      const target = L.playing && !still ? 1 : 0;
      amp += (target - amp) * 0.08;
      L.pluck *= 0.965;
      const a = (amp * 3.2 + L.pluck * 9) * (still ? 0 : 1), t = (now - t0) / 1000;

      ctx.clearRect(0, 0, w, h);
      ctx.lineCap = "round";
      ctx.strokeStyle = colors.line; // the string beyond the playhead, at rest
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(xp, mid); ctx.lineTo(w - pad, mid); ctx.stroke();

      ctx.strokeStyle = colors.accent; // the string behind it
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      const length = xp - pad, modes = Math.max(1, Math.round(length / 70));
      for (let x = 0; x <= length; x += 2) {
        const y = mid + Math.sin((Math.PI * modes * x) / Math.max(length, 1)) * a * Math.cos(t * 9 + L.pluck * 4);
        x === 0 ? ctx.moveTo(pad + x, y) : ctx.lineTo(pad + x, y);
      }
      ctx.lineTo(xp, mid); ctx.stroke();

      ctx.fillStyle = colors.accent; // the playhead
      ctx.beginPath(); ctx.arc(xp, mid, L.drag != null ? 7 : 5, 0, Math.PI * 2); ctx.fill();

      raf = 0;
      if ((L.playing || amp > 0.01 || L.pluck > 0.01) && !document.hidden) raf = requestAnimationFrame(draw);
    };
    const run = () => { if (!raf) raf = requestAnimationFrame(draw); };
    kick.current = run;

    fit(); run();
    const ro = new ResizeObserver(() => { fit(); run(); });
    ro.observe(el);
    const mo = new MutationObserver(() => { readColors(); run(); }); // a theme change
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    document.addEventListener("visibilitychange", run);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); mo.disconnect(); document.removeEventListener("visibilitychange", run); };
  }, []);

  useEffect(() => kick.current(), [status, duration]);

  const at = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left - 8) / (r.width - 16)));
  };
  const down = (e: React.PointerEvent) => { canvas.current!.setPointerCapture(e.pointerId); live.current.drag = at(e); kick.current(); };
  const move = (e: React.PointerEvent) => { if (live.current.drag != null) { live.current.drag = at(e); kick.current(); } };
  const up = (e: React.PointerEvent) => {
    if (live.current.drag == null) return;
    seek(at(e) * live.current.duration);
    live.current.drag = null;
    live.current.pluck = 1;
    kick.current();
  };
  const key = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 5000 : e.key === "ArrowLeft" ? -5000 : 0;
    if (step) { e.preventDefault(); seek(getPositionMs() + step); live.current.pluck = 0.6; kick.current(); }
  };

  return (
    <canvas ref={canvas} className="string" style={{ height }} role="slider" tabIndex={0} aria-label="Seek"
      aria-valuemin={0} aria-valuemax={Math.round(duration / 1000)} aria-valuenow={Math.round(getPositionMs() / 1000)}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { live.current.drag = null; kick.current(); }} onKeyDown={key} />
  );
}

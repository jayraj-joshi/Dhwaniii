// The queue as a pure reducer over PlaybackState (planning.md §10.3, D34). The server only stores this state and relays it.
import type { Ctx, PlaybackState } from "../types";

/** Maps a track id to something comparable (its primary artist) so shuffle can avoid back-to-back repeats. */
export type KeyOf = (trackId: number) => unknown;

export const EMPTY: PlaybackState = {
  context: null, context_tracks: [], order: [], context_pos: 0, user_queue: [],
  current: null, shuffle: false, repeat: "off", position_ms: 0, playing: false,
};

const identity = (n: number) => Array.from({ length: n }, (_, i) => i);

/** Fisher–Yates, then one repair pass that swaps a same-artist neighbour with the nearest later track by someone else (§9.6). */
export function smartShuffle(items: number[], key: (x: number) => unknown, rng = Math.random, first?: number): number[] {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  if (first !== undefined) a.unshift(first);
  for (let i = 1; i < a.length; i++) {
    if (key(a[i]) !== key(a[i - 1])) continue;
    const j = a.findIndex((x, k) => k > i && key(x) !== key(a[i - 1]));
    if (j > 0) [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const posKey = (s: PlaybackState, key: KeyOf) => (p: number) => key(s.context_tracks[p]);
const at = (s: PlaybackState, pos: number) => ({ track_id: s.context_tracks[s.order[pos]], from: "context" as const });

export function playContext(s: PlaybackState, ctx: Ctx, ids: number[], start: number, key: KeyOf): PlaybackState {
  if (!ids.length) return s;
  const n = ids.length;
  const k = (p: number) => key(ids[p]);
  const order = s.shuffle ? smartShuffle(identity(n).filter((p) => p !== start), k, Math.random, start) : identity(n);
  return {
    ...s, context: ctx, context_tracks: ids, order, context_pos: s.shuffle ? 0 : start,
    current: { track_id: ids[start], from: "context" }, position_ms: 0, playing: true,
  };
}

export const playNext = (s: PlaybackState, ids: number[]): PlaybackState => ({ ...s, user_queue: [...ids, ...s.user_queue] });
export const addToQueue = (s: PlaybackState, ids: number[]): PlaybackState => ({ ...s, user_queue: [...s.user_queue, ...ids] });

/** The next state, or null when the queue has run out (the caller then stops or starts autoplay). */
export function next(s: PlaybackState, o: { auto?: boolean }, key: KeyOf): PlaybackState | null {
  if (!s.current) return null;
  if (o.auto && s.repeat === "one") return { ...s, position_ms: 0 };
  if (s.user_queue.length) {
    const [id, ...rest] = s.user_queue; // context_pos stays put: the context resumes after the user's items
    return { ...s, user_queue: rest, current: { track_id: id, from: "user" }, position_ms: 0 };
  }
  const pos = s.context_pos + 1;
  if (pos < s.order.length) return { ...s, context_pos: pos, current: at(s, pos), position_ms: 0 };
  if (s.repeat === "all" && s.order.length) {
    const order = s.shuffle ? smartShuffle(identity(s.order.length), posKey(s, key)) : identity(s.order.length);
    const wrapped = { ...s, order, context_pos: 0 };
    return { ...wrapped, current: at(wrapped, 0), position_ms: 0 };
  }
  return null;
}

/** The caller seeks to 0 instead when more than 3 s have played; this is the "go to the previous track" half. */
export function previous(s: PlaybackState): PlaybackState {
  if (!s.current) return s;
  if (s.current.from === "user" && s.order.length) return { ...s, current: at(s, s.context_pos), position_ms: 0 };
  if (s.context_pos > 0) return { ...s, context_pos: s.context_pos - 1, current: at(s, s.context_pos - 1), position_ms: 0 };
  return { ...s, position_ms: 0 };
}

/** `index` is the position in user_queue, or in `order` for the context. */
export function jumpTo(s: PlaybackState, section: "user" | "context", index: number): PlaybackState {
  if (section === "user") {
    const id = s.user_queue[index];
    if (id === undefined) return s;
    return { ...s, user_queue: s.user_queue.slice(index + 1), current: { track_id: id, from: "user" }, position_ms: 0 };
  }
  if (index < 0 || index >= s.order.length) return s;
  return { ...s, context_pos: index, current: at(s, index), position_ms: 0 };
}

export function remove(s: PlaybackState, section: "user" | "context", index: number): PlaybackState {
  if (section === "user") return { ...s, user_queue: s.user_queue.filter((_, i) => i !== index) };
  if (index <= s.context_pos) return s; // only upcoming items can be removed
  return { ...s, order: s.order.filter((_, i) => i !== index) };
}

export function toggleShuffle(s: PlaybackState, key: KeyOf): PlaybackState {
  if (s.shuffle) { // back to the context's own order, keeping the current track's place in it
    const here = s.order[s.context_pos] ?? 0;
    return { ...s, shuffle: false, order: identity(s.order.length), context_pos: here };
  }
  if (!s.order.length) return { ...s, shuffle: true };
  const k = posKey(s, key);
  if (s.current?.from === "context") {
    const cur = s.order[s.context_pos];
    return { ...s, shuffle: true, order: smartShuffle(s.order.filter((p) => p !== cur), k, Math.random, cur), context_pos: 0 };
  }
  const played = s.order.slice(0, s.context_pos + 1); // a user-queued track is playing: only the upcoming part is shuffled
  return { ...s, shuffle: true, order: [...played, ...smartShuffle(s.order.slice(s.context_pos + 1), k)] };
}

export const cycleRepeat = (s: PlaybackState): PlaybackState =>
  ({ ...s, repeat: s.repeat === "off" ? "all" : s.repeat === "all" ? "one" : "off" });

/** The next n track ids without changing anything; used to pre-cache. */
export function peekNext(s: PlaybackState, n: number): number[] {
  const out = [...s.user_queue];
  for (let p = s.context_pos + 1; out.length < n && p < s.order.length; p++) out.push(s.context_tracks[s.order[p]]);
  return out.slice(0, n);
}

/** Autoplay (D51): append new tracks to the end of the order, unshuffled even when shuffle is on. */
export function extendContext(s: PlaybackState, ids: number[]): PlaybackState {
  const have = new Set(s.context_tracks);
  const fresh = [...new Set(ids)].filter((id) => !have.has(id));
  const base = s.context_tracks.length;
  return { ...s, context_tracks: [...s.context_tracks, ...fresh], order: [...s.order, ...fresh.map((_, i) => base + i)] };
}

export const isLastInContext = (s: PlaybackState) => s.current?.from === "context" && s.context_pos === s.order.length - 1;

/** What the queue panel shows: what's playing, the user's items, and the rest of the context. */
export function view(s: PlaybackState) {
  return {
    now: s.current?.track_id ?? null,
    user: s.user_queue,
    context: s.order.slice(s.context_pos + 1).map((p, i) => ({ id: s.context_tracks[p], orderIndex: s.context_pos + 1 + i })),
  };
}

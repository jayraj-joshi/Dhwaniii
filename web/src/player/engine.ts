// The player: one <audio> element (the most reliable choice for lock-screen and background playback, D36), the queue reducer,
// Media Session, play-history reporting (D38), WebSocket sync (D34, D35) and the autoplay rules (D51).
import { create } from "zustand";
import { api, asset, streamUrl } from "../lib/api";
import { deviceId } from "../lib/device";
import { getTrack, ensureTracks, remember, useTracks } from "../lib/tracks";
import { toast } from "../lib/toast";
import { useImports } from "../lib/imports";
import { send } from "../lib/ws";
import type { Ctx, DeviceInfo, PlaybackState, RemoteTrack, Track } from "../types";
import * as Q from "./queue";

export type PlayItem = Track | RemoteTrack;
export const isTrack = (x: PlayItem): x is Track => "id" in x;

interface PlayerStore {
  s: PlaybackState;
  status: "idle" | "loading" | "playing" | "paused";
  positionMs: number;
  durationMs: number;
  /** At the end of a list, waiting for autoplay to deliver more songs (D51). */
  finding: boolean;
  autoplay: boolean;
  activeDevice: string | null;
  remotePlaying: boolean;
  devices: DeviceInfo[];
  volume: number;
}

const VOL = "resonance.volume";
const readVolume = () => { try { const v = Number(localStorage.getItem(VOL)); return v > 0 && v <= 1 ? v : 1; } catch { return 1; } };

export const usePlayer = create<PlayerStore>(() => ({
  s: Q.EMPTY, status: "idle", positionMs: 0, durationMs: 0, finding: false, autoplay: true,
  activeDevice: null, remotePlaying: false, devices: [], volume: readVolume(),
}));

const get = () => usePlayer.getState();
const patch = (p: Partial<PlayerStore>) => usePlayer.setState(p);
const keyOf: Q.KeyOf = (id) => useTracks.getState().byId[id]?.artists[0]?.id ?? `t${id}`;

const audio = new Audio();
audio.preload = "auto";
let loadedVid: string | null = null;
let token = 0; // bumped on every track start, so a slow metadata fetch can't overwrite a newer choice
let pendingSeek = 0;
let errorStreak = 0;
let pendingRadio: { ctx: string; seed: number; ids: number[] } | null = null;
let findingTimer: ReturnType<typeof setTimeout> | undefined;
let heartbeat: ReturnType<typeof setInterval> | undefined;

export const getPositionMs = () => audio.currentTime * 1000;

// --- state ------------------------------------------------------------------------------

/** Replace the playback state and tell the profile's other devices (the server stores it and relays it). */
function setS(next: PlaybackState, broadcast = true) {
  patch({ s: next });
  if (broadcast) send({ type: "queue_update", state: { ...next, position_ms: Math.round(getPositionMs()) } });
}

function sendPlayerState() {
  const cur = get().s.current;
  if (!cur) return;
  send({ type: "player_state", track_id: cur.track_id, position_ms: Math.round(getPositionMs()), playing: !audio.paused, at: Date.now() });
}

const ctxKey = (c: Ctx | null) => (c ? `${c.type}:${c.id}` : "");

// --- play history (D38) -----------------------------------------------------------------

const HIST = "resonance.history";
interface Play { track_id: number; played_at: string; ms_played: number; skipped: boolean; context: string | null; device_id: string }
let hist = { trackId: 0, ctx: null as string | null, startedAt: 0, ms: 0, tick: 0 };

function historyContext(s: PlaybackState): string | null {
  if (s.current?.from === "user") return "queue";
  const c = s.context;
  if (!c) return null;
  if (c.type === "liked") return "liked";
  return /^\d+$/.test(c.id) && ["playlist", "album", "artist", "radio"].includes(c.type) ? `${c.type}:${c.id}` : null;
}

const histTick = () => { if (hist.tick) { const n = performance.now(); hist.ms += n - hist.tick; hist.tick = n; } };
const histOpen = (trackId: number) => { hist = { trackId, ctx: historyContext(get().s), startedAt: Date.now(), ms: 0, tick: audio.paused ? 0 : performance.now() }; };

function histClose(natural: boolean, keepalive = false) {
  histTick();
  if (hist.trackId && hist.ms >= 1000) {
    const play: Play = { track_id: hist.trackId, played_at: new Date(hist.startedAt).toISOString(), ms_played: Math.round(hist.ms), skipped: !natural && hist.ms < 30_000, context: hist.ctx, device_id: deviceId };
    writeHistory([...readHistory(), play]);
  }
  hist = { trackId: 0, ctx: null, startedAt: 0, ms: 0, tick: 0 };
  void flushHistory(keepalive);
}

function readHistory(): Play[] { try { return JSON.parse(localStorage.getItem(HIST) || "[]"); } catch { return []; } }
function writeHistory(p: Play[]) { try { localStorage.setItem(HIST, JSON.stringify(p.slice(-2000))); } catch { /* private mode */ } }

let flushing = false;
/** Plays wait in localStorage until the server has them, so a flaky connection or a closed tab loses nothing. */
export async function flushHistory(keepalive = false) {
  if (flushing) return;
  const batch = readHistory().slice(0, 500);
  if (!batch.length) return;
  flushing = true;
  try {
    await api.post("/api/me/history", batch, { keepalive });
    writeHistory(readHistory().slice(batch.length));
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 422 || status === 401) writeHistory(readHistory().slice(batch.length)); // the server will never accept these
  } finally { flushing = false; }
}

// --- loading and playing ------------------------------------------------------------------

function applyVolume(gainDb: number | null | undefined) {
  const gain = gainDb && gainDb < 0 ? 10 ** (gainDb / 20) : 1; // quiet tracks can't be boosted without Web Audio, loud ones are trimmed (D32, D36)
  audio.volume = Math.min(1, get().volume * gain);
}

/** Called synchronously from the tap: iOS only lets play() start inside the user's gesture, and awaiting the network first would lose it. */
function prime(videoId: string) {
  histClose(false);
  if (loadedVid === videoId) { if (audio.currentTime > 0.5) audio.currentTime = 0; }
  else { audio.src = streamUrl(videoId); loadedVid = videoId; }
  pendingSeek = 0;
  patch({ status: "loading", finding: false });
  clearTimeout(findingTimer);
  audio.play().catch(onPlayRejected);
}

function onPlayRejected(e: unknown) {
  const name = (e as Error)?.name;
  if (name === "AbortError") return; // a newer track replaced this one
  patch({ status: "paused" });
  if (name === "NotAllowedError") toast("Tap play to start");
}

function playNow() { audio.play().catch(onPlayRejected); }

function loadTrack(track: Track, o: { autoplay: boolean; seekMs?: number; fresh?: boolean }) {
  if (loadedVid !== track.video_id) { audio.src = streamUrl(track.video_id); loadedVid = track.video_id; }
  else if (o.fresh && audio.currentTime > 0.5) audio.currentTime = 0;
  pendingSeek = o.seekMs ?? 0;
  if (pendingSeek && audio.readyState >= 1) { audio.currentTime = pendingSeek / 1000; pendingSeek = 0; }
  applyVolume(track.gain_db);
  patch({ durationMs: track.duration_ms ?? 0, positionMs: o.seekMs ?? 0, status: o.autoplay ? "loading" : "paused" });
  histOpen(track.id);
  setMediaSession(track);
  if (o.autoplay) playNow(); else audio.pause();
  void precache();
  sendPlayerState();
}

/** Load whatever the state says is current. */
async function startCurrent(o: { autoplay: boolean; seekMs?: number }) {
  const my = ++token;
  const cur = get().s.current;
  if (!cur) { audio.pause(); patch({ status: "idle" }); return; }
  try {
    const track = await getTrack(cur.track_id);
    if (my === token) loadTrack(track, { ...o, fresh: true });
  } catch (e) {
    if (my === token) { patch({ status: "paused" }); toast((e as Error).message, "error"); }
  }
}

/** A restored session shows the track and position but doesn't touch the stream until play is pressed (loading it would start a download). */
async function showPaused(positionMs: number) {
  const my = ++token;
  const cur = get().s.current;
  if (!cur) return;
  try {
    const track = await getTrack(cur.track_id);
    if (my !== token) return;
    patch({ durationMs: track.duration_ms ?? 0, positionMs, status: "paused" });
    setMediaSession(track);
  } catch { /* the bar stays empty until the next state arrives */ }
}

const asked = new Set<string>();
/** Ask the server to cache the next two tracks while the current one plays (D34). */
async function precache() {
  for (const id of Q.peekNext(get().s, 2)) {
    try {
      const t = await getTrack(id);
      const state = useTracks.getState().cacheByVid[t.video_id] ?? t.cache_state;
      if (state === "cached" || state === "downloading" || asked.has(t.video_id)) continue;
      asked.add(t.video_id);
      api.post(`/api/tracks/${t.video_id}/cache?priority=high`).catch(() => asked.delete(t.video_id));
    } catch { /* not worth interrupting playback for */ }
  }
}

// --- public actions ------------------------------------------------------------------------

async function resolve(items: PlayItem[]): Promise<Track[]> {
  const remotes = items.filter((x): x is RemoteTrack => !isTrack(x));
  const ensured = remotes.length ? await ensureTracks(remotes) : [];
  remember(items.filter(isTrack));
  let k = 0;
  return items.map((x) => (isTrack(x) ? x : ensured[k++]));
}

/** Play `items[start]` and keep the rest as the context. Works for library tracks and YouTube results alike. */
export async function playItems(ctx: Ctx | ((t: Track[]) => Ctx), items: PlayItem[], start = 0, o: { shuffle?: boolean } = {}) {
  const first = items[start];
  if (!first) return;
  prime(first.video_id);
  const my = ++token;
  let tracks: Track[];
  try { tracks = await resolve(items); }
  catch (e) { audio.pause(); patch({ status: "paused" }); toast((e as Error).message, "error"); return; }
  if (my !== token) return;
  pendingRadio = null;
  const c = typeof ctx === "function" ? ctx(tracks) : ctx;
  let s = get().s;
  if (o.shuffle !== undefined && o.shuffle !== s.shuffle) s = { ...s, shuffle: o.shuffle };
  s = Q.playContext(s, c, tracks.map((t) => t.id), start, keyOf);
  setS(s);
  loadTrack(tracks[start], { autoplay: true });
}

/** A song picked by hand starts a radio seeded by it (D51): it plays at once and autoplay fills in what comes next. */
export const playSong = (item: PlayItem) =>
  playItems((t) => ({ type: "radio", id: String(t[0].id), name: `Based on ${t[0].title}` }), [item], 0);

export async function queueItems(items: PlayItem[], where: "next" | "end") {
  try {
    const tracks = await resolve(items);
    const ids = tracks.map((t) => t.id);
    if (!get().s.current) { void playItems({ type: "queue", id: "0", name: "Queue" }, tracks, 0); return; }
    setS(where === "next" ? Q.playNext(get().s, ids) : Q.addToQueue(get().s, ids));
    toast(where === "next" ? "Playing next" : "Added to queue");
    void precache();
  } catch (e) { toast((e as Error).message, "error"); }
}

export function togglePlay() {
  const { s } = get();
  if (!s.current) return;
  if (!audio.paused) { audio.pause(); return; }
  const track = useTracks.getState().byId[s.current.track_id];
  if (track && loadedVid === track.video_id) { patch({ status: "loading" }); playNow(); }
  else if (track) loadTrack(track, { autoplay: true, seekMs: get().positionMs }); // synchronous, so the tap still counts as the gesture
  else void startCurrent({ autoplay: true, seekMs: get().positionMs });
}

export function seek(ms: number) {
  const max = get().durationMs || Infinity;
  const to = Math.max(0, Math.min(ms, max));
  if (loadedVid && audio.readyState >= 1) audio.currentTime = to / 1000; else pendingSeek = to;
  patch({ positionMs: to });
  sendPlayerState();
}

export function skipNext() { advance(false); }

export function skipPrev() {
  if (getPositionMs() > 3000 || !get().s.current) { seek(0); return; }
  histClose(false);
  const s = Q.previous(get().s);
  setS({ ...s, playing: true });
  void startCurrent({ autoplay: true });
}

export function toggleShuffle() { setS(Q.toggleShuffle(get().s, keyOf)); }
export function cycleRepeat() { setS(Q.cycleRepeat(get().s)); }

export function jump(section: "user" | "context", index: number) {
  histClose(false);
  setS({ ...Q.jumpTo(get().s, section, index), playing: true });
  void startCurrent({ autoplay: true });
}

export function removeQueued(section: "user" | "context", index: number) { setS(Q.remove(get().s, section, index)); }

export function setVolume(v: number) {
  try { localStorage.setItem(VOL, String(v)); } catch { /* private mode */ }
  patch({ volume: v });
  const t = useTracks.getState().byId[get().s.current?.track_id ?? -1];
  applyVolume(t?.gain_db);
}

/** Next track, or what happens when the queue runs out (autoplay, D51). */
function advance(auto: boolean) {
  histClose(auto);
  const n = Q.next(get().s, { auto }, keyOf);
  if (n) { setS({ ...n, playing: true }); void startCurrent({ autoplay: true }); return; }
  onQueueEnd();
}

const AUTOPLAY_CONTEXTS = new Set(["radio", "playlist", "album", "artist", "liked"]);

function onQueueEnd() {
  const { s, autoplay } = get();
  if (pendingRadio && pendingRadio.ctx === ctxKey(s.context)) { startPendingRadio(); return; }
  if (autoplay && s.repeat === "off" && s.context && AUTOPLAY_CONTEXTS.has(s.context.type)) {
    patch({ finding: true, status: "paused" }); // wait up to 20 s for the songs the server is looking for
    clearTimeout(findingTimer);
    findingTimer = setTimeout(stop, 20_000);
    return;
  }
  stop();
}

function stop() {
  clearTimeout(findingTimer);
  audio.pause();
  patch({ finding: false, status: "paused", positionMs: 0, s: { ...get().s, playing: false, position_ms: 0 } });
  sendPlayerState();
}

function startPendingRadio() {
  const r = pendingRadio;
  if (!r) return;
  pendingRadio = null;
  void getTrack(r.seed).then((seed) => {
    const ctx: Ctx = { type: "radio", id: String(r.seed), name: `Based on ${seed.title}` };
    setS(Q.playContext(get().s, ctx, r.ids, 0, keyOf));
    void startCurrent({ autoplay: true });
  });
}

interface AutoplayMsg { context: { type: string; id: string | number }; after_len: number; seed_track_id: number; tracks: Track[] }

function onAutoplay(m: AutoplayMsg) {
  const { s, finding } = get();
  const same = s.context && s.context.type === m.context.type && String(s.context.id) === String(m.context.id);
  if (!same || s.context_tracks.length !== m.after_len) return; // stale: the user changed song meanwhile
  if (!m.tracks.length) { if (finding) stop(); return; }
  remember(m.tracks);
  const ids = m.tracks.map((t) => t.id);
  if (s.context!.type === "radio") {
    setS(Q.extendContext(s, ids));
    if (finding) { patch({ finding: false }); clearTimeout(findingTimer); advance(true); }
    void precache();
  } else {
    pendingRadio = { ctx: ctxKey(s.context), seed: m.seed_track_id, ids }; // starts when this list ends, as a radio of only the new songs
    if (finding) { patch({ finding: false }); clearTimeout(findingTimer); startPendingRadio(); }
  }
}

// --- audio element events --------------------------------------------------------------------

audio.addEventListener("playing", () => {
  errorStreak = 0;
  hist.tick = hist.trackId ? performance.now() : 0;
  patch({ status: "playing", s: { ...get().s, playing: true } });
  if (navigator.mediaSession) navigator.mediaSession.playbackState = "playing";
  sendPlayerState();
});

audio.addEventListener("pause", () => {
  if (audio.ended) return;
  histTick();
  hist.tick = 0;
  patch({ status: get().status === "idle" ? "idle" : "paused", s: { ...get().s, playing: false } });
  if (navigator.mediaSession) navigator.mediaSession.playbackState = "paused";
  sendPlayerState();
});

audio.addEventListener("waiting", () => patch({ status: "loading" }));
audio.addEventListener("loadedmetadata", () => {
  if (pendingSeek) { audio.currentTime = pendingSeek / 1000; pendingSeek = 0; }
  if (Number.isFinite(audio.duration) && !get().durationMs) patch({ durationMs: audio.duration * 1000 });
});

let lastMs = 0;
audio.addEventListener("timeupdate", () => {
  patch({ positionMs: getPositionMs() });
  const now = Date.now();
  if (now - lastMs < 1000) return;
  lastMs = now;
  const ms = navigator.mediaSession;
  const d = audio.duration;
  if (ms?.setPositionState && Number.isFinite(d) && d > 0) {
    try { ms.setPositionState({ duration: d, playbackRate: audio.playbackRate, position: Math.min(audio.currentTime, d) }); } catch { /* unsupported */ }
  }
});

audio.addEventListener("ended", () => {
  if (get().s.repeat === "one") {
    histClose(true);
    const id = get().s.current?.track_id;
    audio.currentTime = 0;
    playNow();
    if (id) histOpen(id);
    return;
  }
  advance(true);
});

audio.addEventListener("error", () => {
  if (!audio.currentSrc) return;
  const t = useTracks.getState().byId[get().s.current?.track_id ?? -1];
  toast(t ? `Couldn't play “${t.title}”` : "Couldn't play that track", "error");
  histClose(true);
  if (++errorStreak >= 3) { loadedVid = null; patch({ status: "paused" }); return; } // don't skip through a whole broken playlist
  advance(true);
});

// --- Media Session (lock screen, headphones, car) ----------------------------------------------------

function setMediaSession(track: Track) {
  const ms = navigator.mediaSession;
  if (!ms) return;
  ms.metadata = new MediaMetadata({
    title: track.title, artist: track.artists.map((a) => a.name).join(", "), album: track.album?.title ?? "",
    artwork: [{ src: asset(track.art_url)!, sizes: "600x600", type: "image/jpeg" }],
  });
}

function initMediaSession() {
  const ms = navigator.mediaSession;
  if (!ms) return;
  const on = (a: MediaSessionAction, f: MediaSessionActionHandler) => { try { ms.setActionHandler(a, f); } catch { /* not supported here */ } };
  on("play", togglePlay);
  on("pause", () => audio.pause());
  on("previoustrack", skipPrev);
  on("nexttrack", skipNext);
  on("seekto", (d) => { if (d.seekTime != null) seek(d.seekTime * 1000); });
  // seekbackward/seekforward are left out on purpose: iOS then shows them on the lock screen instead of previous/next.
}

// --- server messages -------------------------------------------------------------------------------

function adopt(state: PlaybackState) {
  patch({ s: { ...state, playing: false }, positionMs: state.position_ms || 0 });
  void showPaused(state.position_ms || 0);
}

/** Everything the WebSocket pushes (§8). `state` is the stored queue on connect; it is loaded paused and never auto-plays. */
export function onServerMessage(m: any) {
  switch (m.type) {
    case "state":
      patch({ devices: m.devices ?? [], activeDevice: m.active_device_id ?? null });
      if (m.state && !get().s.current) adopt(m.state);
      break;
    case "devices":
      patch({ devices: m.list });
      break;
    case "queue_update":
      if (audio.paused) adopt(m.state); // another device is steering; ignore it while this one is playing
      break;
    case "player_state":
      if (audio.paused) patch({ positionMs: m.position_ms ?? 0, remotePlaying: !!m.playing });
      break;
    case "active_device":
      patch({ activeDevice: m.device_id, remotePlaying: m.device_id !== deviceId });
      if (m.device_id !== deviceId) { if (!audio.paused) audio.pause(); }
      else if (m.resume && m.state) { setS({ ...m.state, playing: true }, false); void startCurrent({ autoplay: true, seekMs: m.state.position_ms }); }
      break;
    case "track_cached":
      useTracks.setState((st) => {
        const progress = { ...st.progress };
        delete progress[m.video_id];
        return { cacheByVid: { ...st.cacheByVid, [m.video_id]: "cached" }, progress };
      });
      break;
    case "download_progress":
      useTracks.setState((st) => ({ progress: { ...st.progress, [m.video_id]: m.percent }, cacheByVid: { ...st.cacheByVid, [m.video_id]: "downloading" } }));
      break;
    case "download_failed":
      useTracks.setState((st) => ({ cacheByVid: { ...st.cacheByVid, [m.video_id]: "failed" } }));
      break;
    case "import_progress":
      useImports.setState((st) => ({ progress: { ...st.progress, [m.import_id]: { done: m.done, total: m.total } } }));
      break;
    case "autoplay_tracks":
      onAutoplay(m);
      break;
    case "error":
      toast(m.detail, "error");
      break;
  }
}

// --- lifecycle ---------------------------------------------------------------------------------------

let started = false;
export function startEngine() {
  if (started) return;
  started = true;
  initMediaSession();
  heartbeat = setInterval(() => { if (!audio.paused) sendPlayerState(); }, 15_000);
  addEventListener("pagehide", () => histClose(true, true));
  addEventListener("online", () => void flushHistory());
  void flushHistory();
}

/** Switching profile: stop everything and forget the queue. */
export function resetPlayer() {
  token++;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  loadedVid = null;
  pendingRadio = null;
  clearTimeout(findingTimer);
  hist = { trackId: 0, ctx: null, startedAt: 0, ms: 0, tick: 0 };
  patch({ s: Q.EMPTY, status: "idle", positionMs: 0, durationMs: 0, finding: false, activeDevice: null, remotePlaying: false, devices: [] });
  clearInterval(heartbeat);
  started = false;
}

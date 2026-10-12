import { useEffect } from "react";
import { create } from "zustand";
import type { CacheState, RemoteTrack, Track } from "../types";
import { api, downloadUrl } from "./api";
import { toast } from "./toast";

interface TrackStore {
  byId: Record<number, Track>;
  /** Live overrides: what this device just did, or what the server pushed over the WebSocket. They win over a list's own copy. */
  liked: Record<number, boolean>;
  cacheByVid: Record<string, CacheState>;
  progress: Record<string, number>;
}

export const useTracks = create<TrackStore>(() => ({ byId: {}, liked: {}, cacheByVid: {}, progress: {} }));

export function remember(ts: Track[]) {
  if (!ts.length) return;
  useTracks.setState((s) => {
    const byId = { ...s.byId };
    for (const t of ts) byId[t.id] = t;
    return { byId };
  });
}

/** A Track as it looks right now (likes and download state change after a list was fetched). */
export function useLive(t: Track): Track & { pct?: number } {
  const liked = useTracks((s) => s.liked[t.id]);
  const cache = useTracks((s) => s.cacheByVid[t.video_id]);
  const pct = useTracks((s) => s.progress[t.video_id]);
  return { ...t, liked: liked ?? t.liked, cache_state: cache ?? t.cache_state, pct };
}

const inflight = new Map<number, Promise<Track>>();
export function getTrack(id: number): Promise<Track> {
  const have = useTracks.getState().byId[id];
  if (have) return Promise.resolve(have);
  let p = inflight.get(id);
  if (!p) {
    p = api.get<Track>(`/api/tracks/${id}`).then((t) => { remember([t]); return t; }).finally(() => inflight.delete(id));
    inflight.set(id, p);
  }
  return p;
}

/** Turns YouTube results into library tracks (D13). Order is kept; remote results are created as provisional rows. */
export async function ensureTracks(items: RemoteTrack[]): Promise<Track[]> {
  const out: Track[] = [];
  for (let i = 0; i < items.length; i += 200) {
    out.push(...await api.post<Track[]>("/api/tracks/ensure", items.slice(i, i + 200)));
  }
  remember(out);
  return out;
}

export async function toggleLike(t: Track & { liked: boolean }) {
  const want = !t.liked;
  useTracks.setState((s) => ({ liked: { ...s.liked, [t.id]: want } }));
  try {
    await (want ? api.put(`/api/me/likes/${t.id}`) : api.del(`/api/me/likes/${t.id}`));
    toast(want ? "Added to Liked Songs" : "Removed from Liked Songs");
  } catch (e) {
    useTracks.setState((s) => ({ liked: { ...s.liked, [t.id]: !want } }));
    toast((e as Error).message, "error");
  }
}

/** Saves the track's audio file to the device's downloads, where the file manager sees it. */
export async function downloadToDevice(t: Track) {
  const state = useTracks.getState().cacheByVid[t.video_id] ?? t.cache_state;
  if (state !== "cached") {
    toast("Preparing download — try again in a few seconds");
    await api.post(`/api/tracks/${t.video_id}/cache`).catch(() => {});
    return;
  }
  const a = document.createElement("a");
  a.href = downloadUrl(t.video_id);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** A track by id from the cache, fetching it when missing (the queue only stores ids). */
export function useTrack(id: number | null | undefined): Track | undefined {
  const t = useTracks((s) => (id == null ? undefined : s.byId[id]));
  useEffect(() => { if (id != null && !t) getTrack(id).catch(() => {}); }, [id, t]);
  return t;
}

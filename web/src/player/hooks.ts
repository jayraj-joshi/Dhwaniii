import { useTracks } from "../lib/tracks";
import { usePlayer } from "./engine";

/** The track the player is on, and whether sound is coming out of it. */
export function useNowPlaying() {
  const id = usePlayer((s) => s.s.current?.track_id);
  const track = useTracks((s) => (id == null ? undefined : s.byId[id]));
  const status = usePlayer((s) => s.status);
  return { track, status, playing: status === "playing" || (status === "loading" && !!track) };
}

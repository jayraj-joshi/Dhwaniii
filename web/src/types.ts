// Shapes returned by the API (planning.md §7.1) and the shared PlaybackState (§10.3).

export type CacheState = "cached" | "downloading" | "failed" | "remote";

export interface ArtistRef { id: number; name: string }

export interface Track {
  id: number;
  video_id: string;
  title: string;
  artists: ArtistRef[];
  album: { id: number; title: string } | null;
  duration_ms: number | null;
  art_url: string;
  cache_state: CacheState;
  gain_db: number | null;
  liked: boolean;
}

/** A YouTube result that may not be in the library yet. Also what album and artist pages return. */
export interface RemoteTrack {
  video_id: string;
  title: string;
  artists: { name: string; ytm_channel_id?: string | null }[];
  album: { title: string; ytm_browse_id?: string | null } | null;
  duration_ms: number | null;
  thumb_url: string | null;
  channel?: string | null;
  quality_score?: number;
  in_library: boolean;
  track_id: number | null;
  plays?: number;
  track_number?: number;
}

export interface PlaylistSummary {
  id: number;
  name: string;
  owner_id: number;
  description: string;
  pinned_to_sidebar: boolean;
  track_count: number;
  cover_url: string | null;
  collage: string[];
}

export interface PlaylistDetail extends PlaylistSummary {
  items: { item_id: number; position: number; added_at: string; track: Track }[];
}

export interface ArtistSummary { id: number; name: string; art_url: string }
export interface AlbumSummary { id: number; title: string; artist: { id: number; name: string } | null; year: number | null; art_url: string }

export interface Prefs { theme?: "system" | "light" | "dark"; autoplay?: boolean }
export interface Profile { id: number; name: string; avatar: string; prefs: Prefs }

export interface RecentContext { context: string; kind: string; id: number | null; name: string; art_url: string | null; last_played: string | null }
export type HomeSection =
  | { id: "quick_access" | "jump_back_in"; title: string; items: RecentContext[] }
  | { id: "on_repeat" | "recently_added"; title: string; items: Track[] };

export interface SearchResult {
  query_norm: string;
  cache_hit: boolean;
  top: { kind: "artist"; item: ArtistSummary } | { kind: "song"; item: Track } | null;
  songs: Track[];
  artists: ArtistSummary[];
  albums: AlbumSummary[];
  playlists: PlaylistSummary[];
  youtube: RemoteTrack[];
  youtube_artists: { id: number | null; name: string; ytm_channel_id: string; thumb_url: string | null }[];
  youtube_albums: { id: number | null; title: string; ytm_browse_id: string; year: number | null; artist: string | null; thumb_url: string | null }[];
}

export interface ArtistPage {
  id: number; name: string; bio: string | null; art_url: string; followed: boolean; plays_30d: number;
  popular: RemoteTrack[];
  discography: { id: number | null; ytm_browse_id: string | null; title: string; year: number | null; type: "album" | "single"; thumb_url: string | null }[];
  fans_also_like: { id: number | null; name: string; ytm_channel_id: string; thumb_url: string | null }[];
}

export interface AlbumPage {
  id: number; title: string; year: number | null; art_url: string; saved: boolean;
  artist: { id: number; name: string } | null;
  tracks: RemoteTrack[];
}

export interface LibraryData { liked_count: number; playlists: PlaylistSummary[]; artists: ArtistSummary[]; albums: AlbumSummary[] }
export interface SettingsData { codec: string; storage_limit_bytes: number; storage_used_bytes: number; file_count: number }
export interface DeviceInfo { id: string; name: string; online: boolean; active: boolean; last_seen: string }

export interface Ctx { type: "playlist" | "album" | "artist" | "radio" | "liked" | "search" | "queue"; id: string; name: string }

export interface PlaybackState {
  context: Ctx | null;
  context_tracks: number[];
  order: number[];
  context_pos: number;
  user_queue: number[];
  current: { track_id: number; from: "context" | "user" } | null;
  shuffle: boolean;
  repeat: "off" | "all" | "one";
  position_ms: number;
  playing: boolean;
}

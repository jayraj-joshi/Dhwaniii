-- 001_core: Phase 1 schema (planning.md §5.1). Never edit once applied; add a new migration.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE profiles (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    avatar     text NOT NULL DEFAULT '',
    prefs      jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE artists (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           text NOT NULL,
    name_norm      text NOT NULL,
    ytm_channel_id text UNIQUE,
    image_url      text,
    bio            text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX artists_name_norm_uq ON artists (name_norm) WHERE ytm_channel_id IS NULL;
CREATE INDEX artists_name_trgm ON artists USING gin (name_norm gin_trgm_ops);

CREATE TABLE albums (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title         text NOT NULL,
    title_norm    text NOT NULL,
    artist_id     bigint REFERENCES artists(id),
    year          int,
    cover_path    text,
    ytm_browse_id text UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX albums_artist_title_uq ON albums (artist_id, title_norm) WHERE ytm_browse_id IS NULL;
CREATE INDEX albums_title_trgm ON albums USING gin (title_norm gin_trgm_ops);

CREATE TABLE tracks (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title              text NOT NULL,
    title_norm         text NOT NULL,
    primary_artist_id  bigint NOT NULL REFERENCES artists(id),
    album_id           bigint REFERENCES albums(id),
    duration_ms        int,
    artwork_path       text,
    artist_names_norm  text NOT NULL DEFAULT '',
    album_title_norm   text NOT NULL DEFAULT '',
    artist_title_norm  text NOT NULL DEFAULT '',
    search_vec         tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', title_norm), 'A') ||
        setweight(to_tsvector('simple', artist_names_norm), 'B') ||
        setweight(to_tsvector('simple', album_title_norm), 'C')
    ) STORED,
    metadata_status    text NOT NULL DEFAULT 'provisional'
                       CHECK (metadata_status IN ('provisional', 'enriched', 'manual')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tracks_search_vec ON tracks USING gin (search_vec);
CREATE INDEX tracks_title_trgm ON tracks USING gin (title_norm gin_trgm_ops);
CREATE INDEX tracks_artist_title_trgm ON tracks USING gin (artist_title_norm gin_trgm_ops);
CREATE INDEX tracks_primary_artist ON tracks (primary_artist_id);
CREATE INDEX tracks_album ON tracks (album_id);

CREATE TABLE track_artists (
    track_id  bigint NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    artist_id bigint NOT NULL REFERENCES artists(id),
    role      text NOT NULL CHECK (role IN ('primary', 'featured', 'producer')),
    position  smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, artist_id, role)
);
CREATE INDEX track_artists_artist ON track_artists (artist_id);

CREATE TABLE sources (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    track_id      bigint NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    video_id      text NOT NULL UNIQUE,
    channel       text,
    title_raw     text,
    duration_ms   int,
    quality_score real NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sources_track ON sources (track_id);

CREATE TABLE files (
    source_id     bigint PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    path          text NOT NULL,
    codec         text NOT NULL,
    bitrate       int,
    bytes         bigint NOT NULL,
    loudness_lufs real,
    gain_db       real,
    last_played   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX files_lru ON files ((coalesce(last_played, created_at)));

CREATE TABLE playlists (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id          bigint NOT NULL REFERENCES profiles(id),
    name              text NOT NULL,
    name_norm         text NOT NULL,
    description       text NOT NULL DEFAULT '',
    cover_path        text,
    is_collab         boolean NOT NULL DEFAULT false,
    pinned_to_sidebar boolean NOT NULL DEFAULT false,
    folder_id         bigint,  -- FK added in 005
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX playlists_name_trgm ON playlists USING gin (name_norm gin_trgm_ops);
CREATE INDEX playlists_owner ON playlists (owner_id);

CREATE TABLE playlist_tracks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    playlist_id bigint NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    bigint NOT NULL REFERENCES tracks(id),
    position    numeric NOT NULL,
    added_by    bigint REFERENCES profiles(id),
    added_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX playlist_tracks_pos ON playlist_tracks (playlist_id, position);
CREATE INDEX playlist_tracks_track ON playlist_tracks (track_id);

CREATE TABLE likes (
    profile_id bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    track_id   bigint NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, track_id)
);
CREATE INDEX likes_track ON likes (track_id);

CREATE TABLE play_history (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    track_id   bigint NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    played_at  timestamptz NOT NULL DEFAULT now(),
    ms_played  int NOT NULL DEFAULT 0,
    skipped    boolean NOT NULL DEFAULT false,
    context    text,
    device_id  text
);
CREATE INDEX play_history_profile ON play_history (profile_id, played_at DESC);
CREATE INDEX play_history_track ON play_history (track_id);

CREATE TABLE user_track_stats (
    profile_id  bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    track_id    bigint NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    play_count  int NOT NULL DEFAULT 0,
    skip_count  int NOT NULL DEFAULT 0,
    last_played timestamptz,
    PRIMARY KEY (profile_id, track_id)
);

CREATE TABLE download_jobs (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id   text NOT NULL UNIQUE,
    status     text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    priority   text NOT NULL CHECK (priority IN ('high', 'low')),
    attempts   int NOT NULL DEFAULT 0,
    error      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX download_jobs_queue ON download_jobs (status, priority, created_at);

CREATE TABLE devices (
    id         text PRIMARY KEY,
    profile_id bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name       text NOT NULL,
    last_seen  timestamptz NOT NULL DEFAULT now(),
    state      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX devices_profile ON devices (profile_id);

CREATE TABLE playback_state (
    profile_id       bigint PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    state            jsonb NOT NULL,
    active_device_id text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- 003_discovery (M3.1-B): what a profile follows and saves.
CREATE TABLE follows (
    profile_id bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    artist_id  bigint NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, artist_id)
);

CREATE TABLE saved_albums (
    profile_id bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    album_id   bigint NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, album_id)
);

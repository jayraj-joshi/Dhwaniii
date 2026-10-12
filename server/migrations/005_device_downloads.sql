-- 005_device_downloads (M4.2-B): playlists a device keeps offline. Their tracks' files are pinned against eviction (D17).
CREATE TABLE device_downloads (
    device_id   text NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    playlist_id bigint NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, playlist_id)
);
CREATE INDEX device_downloads_playlist ON device_downloads (playlist_id);

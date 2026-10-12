"""Track rows: ensure_track (D13), denormalized search columns (D23) and the Track serializer (§7.1, D14, D15)."""
from . import config
from .text import normalize, parse_title

REFRESH_SQL = """
UPDATE tracks t SET
    artist_names_norm = n.names,
    album_title_norm  = coalesce(al.title_norm, ''),
    artist_title_norm = trim(n.names || ' ' || t.title_norm),
    updated_at = now()
FROM (
    SELECT t2.id, coalesce((
        SELECT string_agg(s.name_norm, ' ' ORDER BY s.pos)
        FROM (SELECT a.name_norm, min(ta.position) AS pos
              FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
              WHERE ta.track_id = t2.id GROUP BY a.id, a.name_norm) s), '') AS names
    FROM tracks t2 WHERE t2.id = ANY($1::bigint[])
) n
LEFT JOIN tracks tt ON tt.id = n.id
LEFT JOIN albums al ON al.id = tt.album_id
WHERE t.id = n.id
"""


async def refresh_track_search(db, track_ids: list[int]) -> None:
    """Recompute artist_names_norm, album_title_norm, artist_title_norm. Call after any change to a track,
    its artists or its album (D23). `db` is a pool or a connection."""
    await db.execute(REFRESH_SQL, list(track_ids))


# $1 = profile id. Callers append their own WHERE (parameters from $2).
TRACK_SELECT = """
SELECT t.id, ps.video_id, t.title, t.duration_ms, ps.gain_db,
    CASE WHEN ps.cached THEN 'cached'
         WHEN dj.status IN ('queued', 'running') THEN 'downloading'
         WHEN dj.status = 'failed' THEN 'failed'
         ELSE 'remote' END AS cache_state,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('id', a.id, 'name', a.name) ORDER BY ta.position), '[]'::jsonb)
       FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
      WHERE ta.track_id = t.id AND ta.role IN ('primary', 'featured')) AS artists,
    CASE WHEN al.id IS NULL THEN NULL ELSE jsonb_build_object('id', al.id, 'title', al.title) END AS album,
    EXISTS (SELECT 1 FROM likes l WHERE l.track_id = t.id AND l.profile_id = $1) AS liked
FROM tracks t
LEFT JOIN albums al ON al.id = t.album_id
LEFT JOIN LATERAL (
    SELECT s.video_id, f.gain_db, (f.source_id IS NOT NULL) AS cached
    FROM sources s LEFT JOIN files f ON f.source_id = s.id
    WHERE s.track_id = t.id
    ORDER BY (f.source_id IS NOT NULL) DESC, s.quality_score DESC, s.id   -- D15 preferred source
    LIMIT 1
) ps ON true
LEFT JOIN download_jobs dj ON dj.video_id = ps.video_id
"""


def track_json(r) -> dict:
    return {
        "id": r["id"], "video_id": r["video_id"], "title": r["title"], "artists": r["artists"],
        "album": r["album"], "duration_ms": r["duration_ms"], "art_url": f"/api/art/track/{r['id']}",
        "cache_state": r["cache_state"], "gain_db": r["gain_db"], "liked": r["liked"],
    }


async def get_tracks(db, profile_id: int, ids: list[int]) -> list[dict]:
    """Track objects for `ids`, in that order. Unknown ids are skipped."""
    if not ids:
        return []
    rows = await db.fetch(TRACK_SELECT + " WHERE t.id = ANY($2::bigint[])", profile_id, ids)
    by_id = {r["id"]: track_json(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


async def _artist_id(c, name: str, channel_id: str | None) -> int:
    nn = normalize(name)
    if channel_id:
        if row := await c.fetchrow("SELECT id FROM artists WHERE ytm_channel_id = $1", channel_id):
            return row["id"]
        # a provisional row with the same name is the same artist: give it the channel id instead of duplicating
        if row := await c.fetchrow("SELECT id FROM artists WHERE name_norm = $1 AND ytm_channel_id IS NULL", nn):
            await c.execute("UPDATE artists SET ytm_channel_id = $2 WHERE id = $1", row["id"], channel_id)
            return row["id"]
        return await c.fetchval("INSERT INTO artists (name, name_norm, ytm_channel_id) VALUES ($1, $2, $3) RETURNING id", name, nn, channel_id)
    row = await c.fetchrow("SELECT id FROM artists WHERE name_norm = $1 ORDER BY (ytm_channel_id IS NOT NULL) DESC, id LIMIT 1", nn)
    return row["id"] if row else await c.fetchval("INSERT INTO artists (name, name_norm) VALUES ($1, $2) RETURNING id", name, nn)


async def _album_id(c, title: str, browse_id: str | None, artist_id: int) -> int:
    nn = normalize(title)
    if browse_id:
        if row := await c.fetchrow("SELECT id FROM albums WHERE ytm_browse_id = $1", browse_id):
            return row["id"]
        if row := await c.fetchrow("SELECT id FROM albums WHERE artist_id = $1 AND title_norm = $2 AND ytm_browse_id IS NULL", artist_id, nn):
            await c.execute("UPDATE albums SET ytm_browse_id = $2 WHERE id = $1", row["id"], browse_id)
            return row["id"]
        return await c.fetchval("INSERT INTO albums (title, title_norm, artist_id, ytm_browse_id) VALUES ($1, $2, $3, $4) RETURNING id", title, nn, artist_id, browse_id)
    row = await c.fetchrow("SELECT id FROM albums WHERE artist_id = $1 AND title_norm = $2 ORDER BY id LIMIT 1", artist_id, nn)
    return row["id"] if row else await c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ($1, $2, $3) RETURNING id", title, nn, artist_id)


async def ensure_track(pool, hint: dict) -> int:
    """Return the track id for hint["video_id"], creating a provisional artist/album/track/source if it is new (D13).
    Safe to call concurrently: a per-video advisory lock serializes creation."""
    vid = hint["video_id"]
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(hashtext($1))", vid)
        if row := await c.fetchrow("SELECT track_id FROM sources WHERE video_id = $1", vid):
            return row["track_id"]
        arts = [(a["name"], a.get("ytm_channel_id")) for a in hint.get("artists") or [] if a.get("name")]
        parsed = parse_title({"title": hint["title"], "channel": hint.get("channel"), **({"artists": hint["artists"]} if arts else {})})
        if not arts:
            arts = [(n, None) for n in parsed["artists"]] or [("Unknown Artist", None)]
        title = parsed["title"]
        artist_ids = [await _artist_id(c, n, ch) for n, ch in arts]
        album = hint.get("album") or ({"title": parsed["album"]} if parsed["album"] else None)
        album_id = await _album_id(c, album["title"], album.get("ytm_browse_id"), artist_ids[0]) if album and album.get("title") else None
        tid = await c.fetchval(
            "INSERT INTO tracks (title, title_norm, primary_artist_id, album_id, duration_ms) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            title, normalize(title), artist_ids[0], album_id, hint.get("duration_ms"))
        for pos, aid in enumerate(dict.fromkeys(artist_ids)):
            await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, $3, $4)",
                            tid, aid, "primary" if pos == 0 else "featured", pos)
        await c.execute(
            "INSERT INTO sources (track_id, video_id, channel, title_raw, duration_ms, quality_score) VALUES ($1, $2, $3, $4, $5, $6)",
            tid, vid, hint.get("channel"), hint["title"], hint.get("duration_ms"), hint.get("quality_score") or 0)
        await refresh_track_search(c, [tid])
        return tid


# --- download requests (D27) -------------------------------------------------

CACHE_STATE_SQL = """
SELECT (f.source_id IS NOT NULL) AS cached, dj.status
FROM (SELECT $1::text AS v) x
LEFT JOIN sources s ON s.video_id = x.v LEFT JOIN files f ON f.source_id = s.id
LEFT JOIN download_jobs dj ON dj.video_id = x.v
"""

UPSERT_JOB_SQL = """
INSERT INTO download_jobs (video_id, status, priority) VALUES ($1, 'queued', $2)
ON CONFLICT (video_id) DO UPDATE SET
    status   = CASE WHEN download_jobs.status = 'failed' AND $2 = 'low' THEN 'failed'
                    WHEN download_jobs.status = 'running' THEN 'running' ELSE 'queued' END,
    priority = CASE WHEN $2 = 'high' THEN 'high' ELSE download_jobs.priority END,
    attempts = CASE WHEN download_jobs.status IN ('done', 'failed') AND NOT (download_jobs.status = 'failed' AND $2 = 'low')
                    THEN 0 ELSE download_jobs.attempts END,
    error    = CASE WHEN download_jobs.status IN ('done', 'failed') AND NOT (download_jobs.status = 'failed' AND $2 = 'low')
                    THEN NULL ELSE download_jobs.error END,
    updated_at = now()
RETURNING status
"""


async def cache_state_for(pool, video_id: str) -> str:
    r = await pool.fetchrow(CACHE_STATE_SQL, video_id)
    if r["cached"]:
        return "cached"
    return {"queued": "downloading", "running": "downloading", "failed": "failed"}.get(r["status"], "remote")


async def enqueue_download(redis, arq, video_id: str) -> bool:
    """Gate (D27 step 3): the Redis lock stops duplicates; arq's _job_id covers a lock that expired while the job still waits."""
    if await redis.set(f"dl:lock:{video_id}", "1", nx=True, ex=config.DOWNLOAD_LOCK_TTL):
        await arq.enqueue_job("download_track", video_id, _job_id=f"dl:{video_id}")
        return True
    return False


async def request_download(pool, redis, arq, video_id: str, priority: str) -> str:
    """The one entry point for every download request (D27). Returns the video's cache_state."""
    if (await cache_state_for(pool, video_id)) == "cached":
        return "cached"
    status = await pool.fetchval(UPSERT_JOB_SQL, video_id, priority)
    if priority == "high" and status == "queued":
        await enqueue_download(redis, arq, video_id)
    return "failed" if status == "failed" else "downloading"


async def drop_orphans(c, artist_ids: list[int], album_id: int | None) -> None:
    """Delete provisional rows nothing refers to any more (never a row that has a YouTube Music id)."""
    if album_id:
        await c.execute("DELETE FROM albums a WHERE a.id = $1 AND a.ytm_browse_id IS NULL AND NOT EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = a.id)", album_id)
    for aid in artist_ids:
        await c.execute(
            "DELETE FROM artists a WHERE a.id = $1 AND a.ytm_channel_id IS NULL AND NOT EXISTS (SELECT 1 FROM track_artists WHERE artist_id = a.id) "
            "AND NOT EXISTS (SELECT 1 FROM tracks WHERE primary_artist_id = a.id) AND NOT EXISTS (SELECT 1 FROM albums WHERE artist_id = a.id)", aid)


async def apply_music_metadata(pool, track_id: int, parsed: dict, duration_ms: int | None) -> None:
    """Upgrade a provisional track with authoritative metadata from a download (D13). Never touches `manual` rows."""
    async with pool.acquire() as c, c.transaction():
        status = await c.fetchval("SELECT metadata_status FROM tracks WHERE id = $1 FOR UPDATE", track_id)
        names = list(dict.fromkeys(parsed["artists"] + parsed["featured"]))
        if status == "manual" or not names:
            return
        old = await c.fetchrow("SELECT album_id, primary_artist_id, (SELECT coalesce(array_agg(artist_id), '{}') FROM track_artists WHERE track_id = $1) AS credited "
                               "FROM tracks WHERE id = $1", track_id)
        artist_ids = list(dict.fromkeys([await _artist_id(c, n, None) for n in names]))
        album_id = await _album_id(c, parsed["album"], None, artist_ids[0]) if parsed["album"] else None
        await c.execute(
            "UPDATE tracks SET title = $2, title_norm = $3, primary_artist_id = $4, album_id = coalesce($5, album_id), "
            "duration_ms = coalesce($6, duration_ms) WHERE id = $1", track_id, parsed["title"], normalize(parsed["title"]),
            artist_ids[0], album_id, duration_ms)
        await c.execute("DELETE FROM track_artists WHERE track_id = $1", track_id)
        for pos, aid in enumerate(artist_ids):
            await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, $3, $4)",
                            track_id, aid, "primary" if pos == 0 else "featured", pos)
        await drop_orphans(c, [a for a in {old["primary_artist_id"], *old["credited"]} if a not in artist_ids],
                           old["album_id"] if album_id and old["album_id"] != album_id else None)
        await refresh_track_search(c, [track_id])


PREFERRED_SOURCES_SQL = """
SELECT DISTINCT ON (s.track_id) s.track_id, s.video_id
FROM sources s LEFT JOIN files f ON f.source_id = s.id
WHERE s.track_id = ANY($1::bigint[])
ORDER BY s.track_id, (f.source_id IS NOT NULL) DESC, s.quality_score DESC, s.id   -- D15
"""


async def request_downloads_for_tracks(pool, redis, arq, track_ids: list[int], priority: str) -> None:
    """Saving a track (like, playlist add, import) fetches its preferred source (D19). Already-cached tracks cost one query."""
    for r in await pool.fetch(PREFERRED_SOURCES_SQL, list(set(track_ids))):
        await request_download(pool, redis, arq, r["video_id"], priority)


async def attach_source(pool, redis, arq, track_id: int, video_id: str, channel: str | None = None, title: str | None = None,
                        duration_ms: int | None = None) -> None:
    """Make `video_id` this track's preferred source (M2.3-B) and fetch it at high priority. Raises LookupError (no such track) or
    ValueError (the video belongs to another track). The worker deletes the old file when the new one lands."""
    async with pool.acquire() as c, c.transaction():
        if not await c.fetchval("SELECT 1 FROM tracks WHERE id = $1 FOR UPDATE", track_id):
            raise LookupError("No such track")
        owner = await c.fetchval("SELECT track_id FROM sources WHERE video_id = $1", video_id)
        if owner is not None and owner != track_id:
            raise ValueError(f"That video already belongs to track {owner}")
        top = await c.fetchval("SELECT coalesce(max(quality_score), 0) FROM sources WHERE track_id = $1", track_id)
        if owner is None:
            await c.execute("INSERT INTO sources (track_id, video_id, channel, title_raw, duration_ms, quality_score) VALUES ($1, $2, $3, $4, $5, $6)",
                            track_id, video_id, channel, title, duration_ms, top + 1)
        else:
            await c.execute("UPDATE sources SET quality_score = $2 WHERE video_id = $1 AND quality_score < $3", video_id, top + 1, top)  # make it the top one
    await request_download(pool, redis, arq, video_id, "high")

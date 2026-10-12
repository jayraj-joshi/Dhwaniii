"""Metadata enrichment (planning.md M2.1, doc §9.1 stages 4-6): swap a provisional track's guessed metadata for YouTube Music's."""
import asyncio
import difflib
import logging

import httpx

from . import storage, youtube
from .text import normalize
from .tracks import _album_id, _artist_id, drop_orphans, refresh_track_search

log = logging.getLogger("resonance.enrich")
DURATION_SLACK_MS = 10_000
MIN_TITLE_RATIO = 0.5  # the second guard: stops a same-length wrong song from matching


def _get_album(browse_id: str) -> dict:
    return youtube._client().get_album(browse_id)


def _fetch_cover(url: str, dst) -> None:
    """Download the largest cover and make it the 600x600 square (D45)."""
    import tempfile
    from pathlib import Path
    r = httpx.get(url, timeout=15, follow_redirects=True)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "cover"
        src.write_bytes(r.content)
        storage.crop_art(src, dst)


def is_match(cand: dict, title: str, duration_ms: int | None) -> bool:
    if not cand.get("artists"):
        return False
    if duration_ms and cand.get("duration_ms") and abs(cand["duration_ms"] - duration_ms) > DURATION_SLACK_MS:
        return False
    return difflib.SequenceMatcher(None, normalize(cand["title"]), normalize(title)).ratio() >= MIN_TITLE_RATIO


async def merge_artists(pool, src: int, dst: int) -> None:
    """Fold artist `src` into `dst`: tracks, credits and albums move over, then `src` is deleted."""
    async with pool.acquire() as c, c.transaction():
        await c.execute("DELETE FROM track_artists s USING track_artists d WHERE s.artist_id = $1 AND d.artist_id = $2 "
                        "AND d.track_id = s.track_id AND d.role = s.role", src, dst)
        await c.execute("UPDATE track_artists SET artist_id = $2 WHERE artist_id = $1", src, dst)
        for d in await c.fetch("SELECT a.id AS s_album, b.id AS d_album FROM albums a JOIN albums b ON b.artist_id = $2 AND b.title_norm = a.title_norm "
                               "WHERE a.artist_id = $1 AND a.ytm_browse_id IS NULL", src, dst):  # same album under both: keep one
            await c.execute("UPDATE tracks SET album_id = $2 WHERE album_id = $1", d["s_album"], d["d_album"])
            await c.execute("DELETE FROM albums WHERE id = $1", d["s_album"])
        await c.execute("UPDATE albums SET artist_id = $2 WHERE artist_id = $1", src, dst)
        await c.execute("UPDATE tracks SET primary_artist_id = $2 WHERE primary_artist_id = $1", src, dst)
        await c.execute("DELETE FROM artists WHERE id = $1", src)
        ids = [r["id"] for r in await c.fetch(
            "SELECT id FROM tracks WHERE primary_artist_id = $1 OR id IN (SELECT track_id FROM track_artists WHERE artist_id = $1)", dst)]
        await refresh_track_search(c, ids)


async def enrich_track(pool, track_id: int) -> bool:
    """True if the track was enriched. Only `provisional` rows are touched; `manual` and `enriched` are left alone."""
    row = await pool.fetchrow(
        "SELECT t.title, t.duration_ms, t.metadata_status, t.album_id, a.name AS artist FROM tracks t "
        "JOIN artists a ON a.id = t.primary_artist_id WHERE t.id = $1", track_id)
    if row is None or row["metadata_status"] != "provisional":
        return False
    try:
        cands = await asyncio.to_thread(youtube._ytm_search, f"{row['artist']} {row['title']}")
    except Exception as e:
        log.info("enrichment search failed for track %s: %s", track_id, e)
        return False
    m = next((c for c in cands if is_match(c, row["title"], row["duration_ms"])), None)
    if m is None:
        return False

    async with pool.acquire() as c, c.transaction():
        if await c.fetchval("SELECT metadata_status FROM tracks WHERE id = $1 FOR UPDATE", track_id) != "provisional":
            return False  # edited by hand while we were searching
        old_artists = [r["artist_id"] for r in await c.fetch("SELECT artist_id FROM track_artists WHERE track_id = $1", track_id)]
        old_artists.append(await c.fetchval("SELECT primary_artist_id FROM tracks WHERE id = $1", track_id))
        new_artists = list(dict.fromkeys([await _artist_id(c, a["name"], a.get("ytm_channel_id")) for a in m["artists"]]))
        twins = [(r["twin"], r["id"]) for r in await c.fetch(  # a provisional row with the same name as a channel row (stage 5)
            "SELECT k.id, a.id AS twin FROM artists k JOIN artists a ON a.name_norm = k.name_norm AND a.ytm_channel_id IS NULL AND a.id <> k.id "
            "WHERE k.id = ANY($1::bigint[]) AND k.ytm_channel_id IS NOT NULL", new_artists)]
        album = m.get("album")
        album_id = await _album_id(c, album["title"], album.get("ytm_browse_id"), new_artists[0]) if album and album.get("title") else row["album_id"]
        await c.execute("UPDATE tracks SET title = $2, title_norm = $3, primary_artist_id = $4, album_id = $5, metadata_status = 'enriched' WHERE id = $1",
                        track_id, m["title"], normalize(m["title"]), new_artists[0], album_id)
        await c.execute("DELETE FROM track_artists WHERE track_id = $1", track_id)
        for pos, aid in enumerate(new_artists):
            await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, $3, $4)",
                            track_id, aid, "primary" if pos == 0 else "featured", pos)
        await drop_orphans(c, [a for a in old_artists if a not in new_artists], row["album_id"] if row["album_id"] != album_id else None)
        await refresh_track_search(c, [track_id])

    for twin, keep in twins:  # after the commit: merge_artists uses its own connection and would wait on our row lock
        await merge_artists(pool, twin, keep)
    if album and album.get("ytm_browse_id") and album_id:
        await _album_details(pool, track_id, album_id, album["ytm_browse_id"])
    return True


async def _album_details(pool, track_id: int, album_id: int, browse_id: str) -> None:
    """Year and square cover from get_album. The cover also becomes the track's artwork."""
    try:
        info = await asyncio.to_thread(_get_album, browse_id)
    except Exception as e:
        log.info("get_album failed for %s: %s", browse_id, e)
        return
    year = int(info["year"]) if str(info.get("year") or "").isdigit() else None
    rel = storage.art_rel("album", album_id)
    have = await pool.fetchval("SELECT cover_path FROM albums WHERE id = $1", album_id)
    if have and not storage.abs_path(have).exists():  # the row remembers a file that is gone (deleted, or a new AUDIO_DIR): fetch it again
        have = None
    if not have and info.get("thumbnails"):
        try:
            await asyncio.to_thread(_fetch_cover, info["thumbnails"][-1]["url"], storage.abs_path(rel))
            have = rel
        except Exception as e:
            log.info("cover download failed for %s: %s", browse_id, e)
    await pool.execute("UPDATE albums SET year = coalesce(year, $2), cover_path = coalesce($3, cover_path) WHERE id = $1", album_id, year, have)
    if have:
        old = await pool.fetchval("SELECT artwork_path FROM tracks WHERE id = $1", track_id)
        await pool.execute("UPDATE tracks SET artwork_path = $2 WHERE id = $1", track_id, have)
        if old and old != have and old.startswith("art/track/"):
            storage.abs_path(old).unlink(missing_ok=True)  # the video-frame crop is replaced by the album cover


async def write_file_tags(pool, track_id: int, audio_path) -> None:
    """(Re)write the file's tags and cover from the database (D31). Also used when a track is edited (M2.3)."""
    meta = await pool.fetchrow(
        "SELECT t.title, t.artwork_path, al.title AS album, al.year, (SELECT array_agg(a.name ORDER BY ta.position) FROM track_artists ta "
        "JOIN artists a ON a.id = ta.artist_id WHERE ta.track_id = t.id) AS artists "
        "FROM tracks t LEFT JOIN albums al ON al.id = t.album_id WHERE t.id = $1", track_id)
    cover = storage.abs_path(meta["artwork_path"]) if meta["artwork_path"] else None
    await asyncio.to_thread(storage.write_tags, audio_path, meta["title"], meta["artists"], meta["album"], meta["year"], cover)

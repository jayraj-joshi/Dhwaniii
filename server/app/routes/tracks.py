from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import enrich, storage, youtube
from ..text import normalize, normalize_query
from ..tracks import (TRACK_SELECT, _album_id, _artist_id, drop_orphans, ensure_track, get_tracks, refresh_track_search,
                      request_download, track_json, attach_source)
from .profiles import current_profile

router = APIRouter(prefix="/api")


class HintArtist(BaseModel):
    name: str
    ytm_channel_id: str | None = None


class HintAlbum(BaseModel):
    title: str
    ytm_browse_id: str | None = None


class TrackHint(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")  # D12
    title: str = Field(min_length=1, max_length=500)
    artists: list[HintArtist] = []
    album: HintAlbum | None = None
    duration_ms: int | None = None
    thumb_url: str | None = None
    channel: str | None = None
    quality_score: float = 0


@router.post("/tracks/ensure")
async def ensure(request: Request, hints: Annotated[list[TrackHint], Body(max_length=200)], p=Depends(current_profile)):
    pool = request.app.state.pool
    ids = [await ensure_track(pool, h.model_dump()) for h in hints]
    return await get_tracks(pool, p["id"], ids)


async def track_detail(pool, profile_id: int, track_id: int) -> dict:
    row = await pool.fetchrow(TRACK_SELECT + " WHERE t.id = $2", profile_id, track_id)
    if row is None:
        raise HTTPException(404, "No such track")
    sources = await pool.fetch(
        "SELECT s.video_id, s.channel, s.quality_score, (f.source_id IS NOT NULL) AS cached "
        "FROM sources s LEFT JOIN files f ON f.source_id = s.id WHERE s.track_id = $1 ORDER BY s.quality_score DESC, s.id", track_id)
    return {**track_json(row), "sources": [dict(x) for x in sources], "has_lyrics": None}  # lyrics are skipped (D49)


@router.get("/tracks/{track_id:int}")
async def get_track(track_id: int, request: Request, p=Depends(current_profile)):
    return await track_detail(request.app.state.pool, p["id"], track_id)


# --- fix metadata (M2.3-B) -----------------------------------------------------------------

class TrackPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    artists: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(None, min_length=1, max_length=10)
    album: str | None = Field(None, max_length=300)  # "" or null clears the album
    year: int | None = Field(None, ge=1000, le=2100)  # set on the track's album


@router.patch("/tracks/{track_id:int}")
async def patch_track(track_id: int, body: TrackPatch, request: Request, p=Depends(current_profile)):
    """Edit by hand. The row becomes `manual`, so enrichment never touches it again, and the file's tags are rewritten."""
    pool, given = request.app.state.pool, body.model_fields_set
    async with pool.acquire() as c, c.transaction():
        t = await c.fetchrow("SELECT title, album_id, primary_artist_id FROM tracks WHERE id = $1 FOR UPDATE", track_id)
        if t is None:
            raise HTTPException(404, "No such track")
        old_artists = {r["artist_id"] for r in await c.fetch("SELECT artist_id FROM track_artists WHERE track_id = $1", track_id)} | {t["primary_artist_id"]}
        primary, album_id = t["primary_artist_id"], t["album_id"]
        if body.artists:
            ids = list(dict.fromkeys([await _artist_id(c, n.strip(), None) for n in body.artists]))
            primary = ids[0]
            await c.execute("DELETE FROM track_artists WHERE track_id = $1", track_id)
            for pos, aid in enumerate(ids):
                await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, $3, $4)", track_id, aid, "primary" if pos == 0 else "featured", pos)
        if "album" in given:
            album_id = await _album_id(c, body.album.strip(), None, primary) if body.album and body.album.strip() else None
        if body.year is not None:
            if album_id is None:
                raise HTTPException(422, "A year needs an album")
            await c.execute("UPDATE albums SET year = $2 WHERE id = $1", album_id, body.year)
        title = body.title.strip() if body.title else t["title"]
        await c.execute("UPDATE tracks SET title = $2, title_norm = $3, primary_artist_id = $4, album_id = $5, metadata_status = 'manual', updated_at = now() WHERE id = $1",
                        track_id, title, normalize(title), primary, album_id)
        new_artists = {r["artist_id"] for r in await c.fetch("SELECT artist_id FROM track_artists WHERE track_id = $1", track_id)} | {primary}
        await drop_orphans(c, list(old_artists - new_artists), t["album_id"] if t["album_id"] != album_id else None)
        await refresh_track_search(c, [track_id])
    f = await pool.fetchrow("SELECT f.source_id, f.path FROM files f JOIN sources s ON s.id = f.source_id WHERE s.track_id = $1 LIMIT 1", track_id)
    if f and (path := storage.resolve(f["path"])):
        await enrich.write_file_tags(pool, track_id, path)
        await pool.execute("UPDATE files SET bytes = $2 WHERE source_id = $1", f["source_id"], path.stat().st_size)  # tags change the size
    return await track_detail(pool, p["id"], track_id)


async def ranked_candidates(state, track_id: int) -> list[dict]:
    """Other YouTube uploads that could be this track, best first, scored against the track's own length. Raises LookupError for no such track."""
    t = await state.pool.fetchrow("SELECT t.title, t.duration_ms, a.name AS artist FROM tracks t JOIN artists a ON a.id = t.primary_artist_id WHERE t.id = $1", track_id)
    if t is None:
        raise LookupError("No such track")
    nq = normalize_query(f"{t['artist']} {t['title']}")
    ranked = youtube.rank_candidates(await youtube.search(state.redis, nq, merge=True), nq, t["duration_ms"])
    known = {r["video_id"]: r["track_id"] for r in await state.pool.fetch(
        "SELECT video_id, track_id FROM sources WHERE video_id = ANY($1::text[])", [c["video_id"] for c in ranked])}
    return [youtube.remote_json(c, c["video_id"] in known, known.get(c["video_id"])) for c in ranked]


@router.get("/tracks/{track_id:int}/candidates")
async def candidates(track_id: int, request: Request, p=Depends(current_profile)):
    try:
        return await ranked_candidates(request.app.state, track_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


class NewSource(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")  # D12
    channel: str | None = None
    title: str | None = Field(None, max_length=500)
    duration_ms: int | None = None


@router.post("/tracks/{track_id:int}/sources", status_code=201)
async def add_source(track_id: int, body: NewSource, request: Request, p=Depends(current_profile)):
    """Choose a different upload for this track. It becomes the preferred source at once (its score is raised above the others),
    is downloaded at high priority, and when its file lands the worker deletes the old file. The track id never changes."""
    s = request.app.state
    try:
        await attach_source(s.pool, s.redis, s.arq, track_id, body.video_id, body.channel, body.title, body.duration_ms)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return await track_detail(s.pool, p["id"], track_id)


ART_SQL = {
    "track": "SELECT coalesce(t.artwork_path, al.cover_path) FROM tracks t LEFT JOIN albums al ON al.id = t.album_id WHERE t.id = $1",
    "album": "SELECT cover_path FROM albums WHERE id = $1",
    "playlist": "SELECT cover_path FROM playlists WHERE id = $1",
    "artist": "SELECT 'art/artist/' || id || '.jpg' FROM artists WHERE id = $1",
}


@router.get("/art/{kind}/{item_id:int}")
async def art(kind: str, item_id: int, request: Request):
    """No profile needed: <img> tags can't send headers (D10)."""
    if kind not in ART_SQL:
        raise HTTPException(404)
    path = storage.resolve(await request.app.state.pool.fetchval(ART_SQL[kind], item_id))
    if path is None:
        raise HTTPException(404, "No artwork")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

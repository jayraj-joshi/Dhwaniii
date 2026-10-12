"""Artist and album pages, follows and saved albums (M3.1-B, D42). YouTube Music data is cached in Redis for 24 h."""
import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import Response

from .. import config, enrich, storage, youtube
from ..tracks import _album_id, _artist_id, get_tracks
from .profiles import current_profile

log = logging.getLogger("resonance.discovery")
router = APIRouter(prefix="/api")
YTM_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
PAGE_TTL = 24 * 3600
MAX_POPULAR = 10


# --- YouTube Music (blocking calls; tests replace these) ------------------------------------

def _yt_artist(channel_id: str) -> dict:
    return youtube._client().get_artist(channel_id)


def _yt_album(browse_id: str) -> dict:
    return youtube._client().get_album(browse_id)


def _yt_search_kind(q: str, kind: str) -> list[dict]:
    return youtube._client().search(q, filter=kind)


async def cached(redis, key: str, fn, *args, ttl: int = PAGE_TTL):
    if hit := await redis.get(key):
        return json.loads(hit)
    data = await asyncio.wait_for(asyncio.to_thread(fn, *args), 15)
    await redis.set(key, json.dumps(data), ex=ttl)
    return data


def big(thumbs) -> str | None:
    return thumbs[-1]["url"] if thumbs else None


def year_of(v) -> int | None:
    return int(v) if str(v or "").isdigit() else None


async def fetch_image(url: str | None, rel: str) -> bool:
    """Best effort: a 600x600 copy of a remote image at `rel` (D45). True if the file exists afterwards."""
    if storage.abs_path(rel).exists():
        return True
    if not url:
        return False
    try:
        await asyncio.to_thread(enrich._fetch_cover, url, storage.abs_path(rel))
        return True
    except Exception as e:
        log.info("image download failed (%s): %s", url, e)
        return False


def track_as_remote(t: dict, plays: int = 0) -> dict:
    """A library Track in the RemoteTrack shape, so lists can mix both."""
    return {"video_id": t["video_id"], "title": t["title"], "artists": [{"name": a["name"], "ytm_channel_id": None} for a in t["artists"]],
            "album": {"title": t["album"]["title"], "ytm_browse_id": None} if t["album"] else None, "duration_ms": t["duration_ms"],
            "thumb_url": None, "channel": None, "quality_score": 0, "in_library": True, "track_id": t["id"], "plays": plays}


async def known_sources(pool, video_ids: list[str]) -> dict[str, int]:
    return {r["video_id"]: r["track_id"] for r in await pool.fetch("SELECT video_id, track_id FROM sources WHERE video_id = ANY($1::text[])", video_ids)}


# --- resolving ids (D42) -------------------------------------------------------------------

async def artist_row(request: Request, ident: str):
    s = request.app.state
    if ident.isdigit():
        row = await s.pool.fetchrow("SELECT * FROM artists WHERE id = $1", int(ident))
    elif ident.startswith("ytm:") and YTM_ID.match(ident[4:]):
        ch = ident[4:]
        row = await s.pool.fetchrow("SELECT * FROM artists WHERE ytm_channel_id = $1", ch)
        if row is None:
            try:
                data = await cached(s.redis, f"ytm:artist:{ch}", _yt_artist, ch)
            except Exception as e:
                raise HTTPException(502, f"YouTube Music is unavailable: {e}")
            async with s.pool.acquire() as c:
                aid = await _artist_id(c, data["name"], ch)
            row = await s.pool.fetchrow("SELECT * FROM artists WHERE id = $1", aid)
    else:
        raise HTTPException(422, "Use an artist id or ytm:<channelId>")
    if row is None:
        raise HTTPException(404, "No such artist")
    return row


async def album_row(request: Request, ident: str):
    s = request.app.state
    if ident.isdigit():
        row = await s.pool.fetchrow("SELECT * FROM albums WHERE id = $1", int(ident))
    elif ident.startswith("ytm:") and YTM_ID.match(ident[4:]):
        b = ident[4:]
        row = await s.pool.fetchrow("SELECT * FROM albums WHERE ytm_browse_id = $1", b)
        if row is None:
            try:
                data = await cached(s.redis, f"ytm:album:{b}", _yt_album, b)
            except Exception as e:
                raise HTTPException(502, f"YouTube Music is unavailable: {e}")
            async with s.pool.acquire() as c, c.transaction():
                art = (data.get("artists") or [{}])[0]
                aid = await _artist_id(c, art["name"], art.get("id")) if art.get("name") else None
                alid = await c.fetchval("SELECT id FROM albums WHERE ytm_browse_id = $1", b)  # a concurrent request may have made it
                alid = alid or await _album_id(c, data["title"], b, aid)
                await c.execute("UPDATE albums SET year = coalesce(year, $2) WHERE id = $1", alid, year_of(data.get("year")))
            row = await s.pool.fetchrow("SELECT * FROM albums WHERE id = $1", alid)
    else:
        raise HTTPException(422, "Use an album id or ytm:<browseId>")
    if row is None:
        raise HTTPException(404, "No such album")
    return row


# --- pages ----------------------------------------------------------------------------------

@router.get("/artists/{ident}")
async def artist_page(ident: str, request: Request, p=Depends(current_profile)):
    s = request.app.state
    row = await artist_row(request, ident)
    aid, ch = row["id"], row["ytm_channel_id"]
    data = None
    if ch:
        try:
            data = await cached(s.redis, f"ytm:artist:{ch}", _yt_artist, ch)
        except Exception as e:
            log.info("artist page for %s without YouTube Music: %s", ch, e)
    if data:
        bio = row["bio"] or data.get("description")
        await s.pool.execute("UPDATE artists SET bio = coalesce(bio, $2), image_url = coalesce(image_url, $3) WHERE id = $1", aid, bio, big(data.get("thumbnails")))
        await fetch_image(big(data.get("thumbnails")), storage.art_rel("artist", aid))

    # popular: what you play most by them first, then YouTube Music's top songs
    lib = await s.pool.fetch(
        "SELECT t.id, coalesce(st.play_count, 0) AS plays FROM tracks t JOIN track_artists ta ON ta.track_id = t.id AND ta.artist_id = $1 "
        "LEFT JOIN user_track_stats st ON st.track_id = t.id AND st.profile_id = $2 ORDER BY plays DESC, t.id LIMIT $3", aid, p["id"], MAX_POPULAR)
    yt_top = [c for c in map(youtube.song_to_remote, (data or {}).get("songs", {}).get("results", [])) if c]
    lib_tracks = {t["id"]: t for t in await get_tracks(s.pool, p["id"], [r["id"] for r in lib])}
    known = await known_sources(s.pool, [c["video_id"] for c in yt_top])
    plays_by_track = {r["id"]: r["plays"] for r in lib}
    # Most-viewed first: YouTube Music's own Top Songs order is kept as given, not re-sorted by this listener's own plays.
    popular = [{**youtube.remote_json({**c, "quality_score": 0}, c["video_id"] in known, known.get(c["video_id"])),
               "plays": plays_by_track.get(known.get(c["video_id"]), 0)} for c in yt_top][:MAX_POPULAR]
    seen = {x["video_id"] for x in popular}
    if data:  # YTM gave fewer than MAX_POPULAR: fill the rest with what this listener plays most (the next-best signal we have)
        for r in lib:
            if r["plays"] > 0 and r["id"] in lib_tracks and lib_tracks[r["id"]]["video_id"] not in seen and len(popular) < MAX_POPULAR:
                popular.append(track_as_remote(lib_tracks[r["id"]], r["plays"]))
    else:  # a local-only artist: no view count exists, so personal play count is the only ordering signal available
        popular = [track_as_remote(lib_tracks[r["id"]], r["plays"]) for r in lib if r["id"] in lib_tracks][:MAX_POPULAR]

    disc = []
    if data:
        browse = [x["browseId"] for k in ("albums", "singles") for x in (data.get(k) or {}).get("results", []) if x.get("browseId")]
        ids = {r["ytm_browse_id"]: r["id"] for r in await s.pool.fetch("SELECT id, ytm_browse_id FROM albums WHERE ytm_browse_id = ANY($1::text[])", browse)}
        for kind, key in (("album", "albums"), ("single", "singles")):
            for x in (data.get(key) or {}).get("results", []):
                if x.get("browseId"):
                    disc.append({"id": ids.get(x["browseId"]), "ytm_browse_id": x["browseId"], "title": x["title"], "year": year_of(x.get("year")), "type": kind,
                                 "thumb_url": big(x.get("thumbnails"))})
    else:
        for r in await s.pool.fetch("SELECT id, title, year, ytm_browse_id FROM albums WHERE artist_id = $1 ORDER BY year DESC NULLS LAST, id", aid):
            disc.append({"id": r["id"], "ytm_browse_id": r["ytm_browse_id"], "title": r["title"], "year": r["year"], "type": "album", "thumb_url": None})

    related = (data or {}).get("related", {}).get("results", [])
    rel_ids = {r["ytm_channel_id"]: r["id"] for r in await s.pool.fetch(
        "SELECT id, ytm_channel_id FROM artists WHERE ytm_channel_id = ANY($1::text[])", [x["browseId"] for x in related if x.get("browseId")])}
    fans = [{"id": rel_ids.get(x["browseId"]), "name": x["title"], "ytm_channel_id": x["browseId"], "thumb_url": big(x.get("thumbnails"))} for x in related if x.get("browseId")]

    plays = await s.pool.fetchval(
        "SELECT count(*) FROM play_history h WHERE h.profile_id = $1 AND h.played_at > now() - interval '30 days' "
        "AND EXISTS (SELECT 1 FROM track_artists ta WHERE ta.track_id = h.track_id AND ta.artist_id = $2)", p["id"], aid)
    fresh = await s.pool.fetchrow("SELECT name, bio FROM artists WHERE id = $1", aid)
    return {"id": aid, "name": fresh["name"], "ytm_channel_id": ch, "bio": fresh["bio"], "art_url": f"/api/art/artist/{aid}",
            "followed": bool(await s.pool.fetchval("SELECT 1 FROM follows WHERE profile_id = $1 AND artist_id = $2", p["id"], aid)),
            "plays_30d": plays, "popular": popular, "discography": disc, "fans_also_like": fans}


@router.get("/albums/{ident}")
async def album_page(ident: str, request: Request, p=Depends(current_profile)):
    s = request.app.state
    row = await album_row(request, ident)
    alid, b = row["id"], row["ytm_browse_id"]
    data = None
    if b:
        try:
            data = await cached(s.redis, f"ytm:album:{b}", _yt_album, b)
        except Exception as e:
            log.info("album page for %s without YouTube Music: %s", b, e)
    if data:
        await s.pool.execute("UPDATE albums SET year = coalesce(year, $2) WHERE id = $1", alid, year_of(data.get("year")))
        if await fetch_image(big(data.get("thumbnails")), storage.art_rel("album", alid)):
            await s.pool.execute("UPDATE albums SET cover_path = coalesce(cover_path, $2) WHERE id = $1", alid, storage.art_rel("album", alid))
        raw = []
        for i, t in enumerate(data.get("tracks") or [], 1):
            c = youtube.song_to_remote({**t, "thumbnails": t.get("thumbnails") or data.get("thumbnails")})
            if c:
                c["album"] = {"title": data["title"], "ytm_browse_id": b}  # so ensuring the track ties it to this album
                raw.append((i, c))
        known = await known_sources(s.pool, [c["video_id"] for _, c in raw])
        tracks = [{**youtube.remote_json({**c, "quality_score": 0}, c["video_id"] in known, known.get(c["video_id"])), "track_number": i} for i, c in raw]
    else:
        ids = [r["id"] for r in await s.pool.fetch("SELECT id FROM tracks WHERE album_id = $1 ORDER BY id", alid)]
        tracks = [{**track_as_remote(t), "track_number": i} for i, t in enumerate(await get_tracks(s.pool, p["id"], ids), 1)]
    fresh = await s.pool.fetchrow("SELECT al.title, al.year, al.cover_path, ar.id AS artist_id, ar.name AS artist_name FROM albums al "
                                  "LEFT JOIN artists ar ON ar.id = al.artist_id WHERE al.id = $1", alid)
    return {"id": alid, "title": fresh["title"], "year": fresh["year"], "ytm_browse_id": b,
            "artist": {"id": fresh["artist_id"], "name": fresh["artist_name"]} if fresh["artist_id"] else None,
            "art_url": f"/api/art/album/{alid}", "saved": bool(await s.pool.fetchval("SELECT 1 FROM saved_albums WHERE profile_id = $1 AND album_id = $2", p["id"], alid)),
            "tracks": tracks}


# --- follow and save ------------------------------------------------------------------------

@router.put("/me/follows/{artist_id}", status_code=204)
async def follow(artist_id: int, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    if not await pool.fetchval("SELECT 1 FROM artists WHERE id = $1", artist_id):
        raise HTTPException(404, "No such artist")
    await pool.execute("INSERT INTO follows (profile_id, artist_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", p["id"], artist_id)
    return Response(status_code=204)


@router.delete("/me/follows/{artist_id}", status_code=204)
async def unfollow(artist_id: int, request: Request, p=Depends(current_profile)):
    await request.app.state.pool.execute("DELETE FROM follows WHERE profile_id = $1 AND artist_id = $2", p["id"], artist_id)
    return Response(status_code=204)


@router.put("/me/albums/{album_id}", status_code=204)
async def save_album(album_id: int, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    if not await pool.fetchval("SELECT 1 FROM albums WHERE id = $1", album_id):
        raise HTTPException(404, "No such album")
    await pool.execute("INSERT INTO saved_albums (profile_id, album_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", p["id"], album_id)
    return Response(status_code=204)


@router.delete("/me/albums/{album_id}", status_code=204)
async def unsave_album(album_id: int, request: Request, p=Depends(current_profile)):
    await request.app.state.pool.execute("DELETE FROM saved_albums WHERE profile_id = $1 AND album_id = $2", p["id"], album_id)
    return Response(status_code=204)


# --- YouTube artist/album results for the search tabs (D25) -----------------------------------

async def remote_kind(request: Request, nq: str, kind: str) -> list[dict]:
    """`kind` is "artists" or "albums". Same-shaped rows either way; `id` is set when the row is already in the database."""
    s = request.app.state
    try:
        raw = await cached(s.redis, f"ys:{kind}:{nq}", _yt_search_kind, nq, kind, ttl=config.SEARCH_CACHE_TTL)
    except Exception as e:
        log.info("YouTube %s search failed (%s): %s", kind, nq, e)
        return []
    if kind == "artists":
        keys = [r["browseId"] for r in raw if r.get("browseId")]
        ids = {r["ytm_channel_id"]: r["id"] for r in await s.pool.fetch("SELECT id, ytm_channel_id FROM artists WHERE ytm_channel_id = ANY($1::text[])", keys)}
        return [{"id": ids.get(r["browseId"]), "name": r["artist"], "ytm_channel_id": r["browseId"], "thumb_url": big(r.get("thumbnails"))} for r in raw if r.get("browseId")]
    keys = [r["browseId"] for r in raw if r.get("browseId")]
    ids = {r["ytm_browse_id"]: r["id"] for r in await s.pool.fetch("SELECT id, ytm_browse_id FROM albums WHERE ytm_browse_id = ANY($1::text[])", keys)}
    return [{"id": ids.get(r["browseId"]), "title": r["title"], "ytm_browse_id": r["browseId"], "year": year_of(r.get("year")),
             "artist": (r.get("artists") or [{}])[0].get("name"), "thumb_url": big(r.get("thumbnails"))} for r in raw if r.get("browseId")]

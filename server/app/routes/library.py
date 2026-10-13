"""The signed-in profile's own data: play history (D38), likes, library and recent (D20)."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from .. import config
from ..text import normalize, normalize_query
from ..tracks import get_tracks, request_downloads_for_tracks
from .playlists import PLAYLIST_SELECT, RECENT_ORDER, owned, playlist_json
from .search import album_json, artist_json, search_songs
from .profiles import current_profile

router = APIRouter(prefix="/api")

CONTEXT = r"^(playlist:\d+|album:\d+|artist:\d+|radio:\d+|liked|search|queue)$"  # D20
COUNT_MS = 30_000  # a play counts after 30 s, or half of a shorter track


class Play(BaseModel):
    track_id: int
    played_at: datetime
    ms_played: int = Field(ge=0, le=86_400_000)
    skipped: bool = False
    context: str | None = Field(None, pattern=CONTEXT)
    device_id: str | None = Field(None, max_length=64)


def is_counted(ms_played: int, duration_ms: int | None) -> bool:
    return ms_played >= (min(COUNT_MS, duration_ms / 2) if duration_ms else COUNT_MS)


@router.post("/me/history", status_code=204)
async def post_history(request: Request, plays: Annotated[list[Play], Body(max_length=500)], p=Depends(current_profile)):
    """Batch, so offline plays can be sent when the connection returns (D38). All or nothing."""
    if not plays:
        return Response(status_code=204)
    async with request.app.state.pool.acquire() as c, c.transaction():
        durations = {r["id"]: r["duration_ms"] for r in await c.fetch(
            "SELECT id, duration_ms FROM tracks WHERE id = ANY($1::bigint[])", list({x.track_id for x in plays}))}
        if missing := sorted({x.track_id for x in plays} - set(durations)):
            raise HTTPException(422, f"Unknown track ids: {missing}")
        await c.executemany(
            "INSERT INTO play_history (profile_id, track_id, played_at, ms_played, skipped, context, device_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [(p["id"], x.track_id, x.played_at, x.ms_played, x.skipped, x.context, x.device_id) for x in plays])
        stats: dict[int, list] = {}  # track -> [counted, skipped, last_played]
        for x in plays:
            s = stats.setdefault(x.track_id, [0, 0, x.played_at])
            s[0] += is_counted(x.ms_played, durations[x.track_id])
            s[1] += x.skipped
            s[2] = max(s[2], x.played_at)
        await c.executemany(
            "INSERT INTO user_track_stats (profile_id, track_id, play_count, skip_count, last_played) VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (profile_id, track_id) DO UPDATE SET play_count = user_track_stats.play_count + EXCLUDED.play_count, "
            "skip_count = user_track_stats.skip_count + EXCLUDED.skip_count, "
            "last_played = greatest(user_track_stats.last_played, EXCLUDED.last_played)",
            [(p["id"], t, s[0], s[1], s[2]) for t, s in stats.items()])
    return Response(status_code=204)


# --- likes (a liked file is pinned against eviction, D17) -----------------------------------

@router.put("/me/likes/{track_id}", status_code=204)
async def like(track_id: int, request: Request, p=Depends(current_profile)):
    s = request.app.state
    if not await s.pool.fetchval("SELECT 1 FROM tracks WHERE id = $1", track_id):
        raise HTTPException(404, "No such track")
    await s.pool.execute("INSERT INTO likes (profile_id, track_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", p["id"], track_id)
    await request_downloads_for_tracks(s.pool, s.redis, s.arq, [track_id], "low")  # D19
    return Response(status_code=204)


@router.delete("/me/likes/{track_id}", status_code=204)
async def unlike(track_id: int, request: Request, p=Depends(current_profile)):
    await request.app.state.pool.execute("DELETE FROM likes WHERE profile_id = $1 AND track_id = $2", p["id"], track_id)
    return Response(status_code=204)


@router.get("/me/likes")
async def list_likes(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), p=Depends(current_profile)):
    pool = request.app.state.pool
    ids = [r["track_id"] for r in await pool.fetch(
        "SELECT track_id FROM likes WHERE profile_id = $1 ORDER BY created_at DESC, track_id DESC OFFSET $2 LIMIT $3", p["id"], offset, limit)]
    return await get_tracks(pool, p["id"], ids)


@router.get("/me/library")
async def library(request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    liked = await pool.fetchval("SELECT count(*) FROM likes WHERE profile_id = $1", p["id"])
    rows = await pool.fetch(PLAYLIST_SELECT + " WHERE p.owner_id = $1 " + RECENT_ORDER, p["id"])
    artists = await pool.fetch("SELECT a.id, a.name FROM follows f JOIN artists a ON a.id = f.artist_id WHERE f.profile_id = $1 ORDER BY f.created_at DESC", p["id"])
    albums = await pool.fetch("SELECT al.id, al.title, al.year, ar.id AS artist_id, ar.name AS artist_name FROM saved_albums s JOIN albums al ON al.id = s.album_id "
                              "LEFT JOIN artists ar ON ar.id = al.artist_id WHERE s.profile_id = $1 ORDER BY s.created_at DESC", p["id"])
    return {"liked_count": liked, "playlists": [playlist_json(r) for r in rows],
            "artists": [artist_json(r) for r in artists], "albums": [album_json(r) for r in albums]}


# --- recent contexts for "Jump back in" (D20) --------------------------------------------

async def recent_contexts(pool, pid: int, limit: int, skip_liked: bool = False) -> list[dict]:
    """Distinct places the profile played from, newest first. `search` and `queue` aren't places, so they are skipped."""
    rows = await pool.fetch(
        "SELECT context, max(played_at) AS last_played FROM play_history WHERE profile_id = $1 AND context IS NOT NULL "
        "AND context NOT IN ('search', 'queue') GROUP BY context ORDER BY last_played DESC LIMIT $2", pid, limit * 3 + 1)
    if skip_liked:
        rows = [r for r in rows if r["context"] != "liked"]
    ids = lambda kind: [int(r["context"].split(":")[1]) for r in rows if r["context"].startswith(kind + ":")]
    named: dict[str, tuple[str, str | None]] = {}  # context -> (name, art_url)
    for r in await pool.fetch(
            "SELECT p.id, p.name, p.cover_path IS NOT NULL AS has_cover, "
            "(SELECT track_id FROM playlist_tracks WHERE playlist_id = p.id ORDER BY position, id LIMIT 1) AS first_track "
            "FROM playlists p WHERE p.owner_id = $1 AND p.id = ANY($2::bigint[])", pid, ids("playlist")):
        named[f"playlist:{r['id']}"] = (r["name"], f"/api/art/playlist/{r['id']}" if r["has_cover"] else
                                        f"/api/art/track/{r['first_track']}" if r["first_track"] else None)
    for r in await pool.fetch("SELECT id, title FROM albums WHERE id = ANY($1::bigint[])", ids("album")):
        named[f"album:{r['id']}"] = (r["title"], f"/api/art/album/{r['id']}")
    for r in await pool.fetch("SELECT id, name FROM artists WHERE id = ANY($1::bigint[])", ids("artist")):
        named[f"artist:{r['id']}"] = (r["name"], f"/api/art/artist/{r['id']}")
    for r in await pool.fetch("SELECT id, title FROM tracks WHERE id = ANY($1::bigint[])", ids("radio")):
        named[f"radio:{r['id']}"] = (f"{r['title']} Radio", f"/api/art/track/{r['id']}")
    newest_like = await pool.fetchval("SELECT track_id FROM likes WHERE profile_id = $1 ORDER BY created_at DESC LIMIT 1", pid)
    if any(r["context"] == "liked" for r in rows):
        named["liked"] = ("Liked Songs", f"/api/art/track/{newest_like}" if newest_like else None)
    out = []
    for r in rows:  # contexts whose playlist/album/... no longer exists are dropped
        if r["context"] in named:
            kind, _, ident = r["context"].partition(":")
            out.append({"context": r["context"], "kind": kind, "id": int(ident) if ident else None,
                        "name": named[r["context"]][0], "art_url": named[r["context"]][1], "last_played": r["last_played"]})
    return out[:limit]

@router.get("/me/recent")
async def recent(request: Request, limit: int = Query(12, ge=1, le=50), p=Depends(current_profile)):
    return await recent_contexts(request.app.state.pool, p["id"], limit)


# --- recent searches (M2.5-B) --------------------------------------------------------------

KEEP_SEARCHES = 50  # stored per profile; the API returns the newest 20


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    video_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{11}$")  # the song played from this search (D12)


SEARCH_ART_SQL = """
SELECT t.id, t.artwork_path IS NOT NULL AS has_art,
       (SELECT video_id FROM sources WHERE track_id = t.id ORDER BY quality_score DESC, id LIMIT 1) AS video_id
FROM tracks t WHERE t.id = ANY($1)
"""


@router.get("/me/searches")
async def list_searches(request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    rows = await pool.fetch(
        "SELECT rs.query, rs.searched_at, rs.video_id, s.track_id FROM recent_searches rs "
        "LEFT JOIN sources s ON s.video_id = rs.video_id WHERE rs.profile_id = $1 ORDER BY rs.searched_at DESC LIMIT 20", p["id"])
    picks = []  # (row, track id or None)
    for r in rows:
        track_id = r["track_id"]
        if not r["video_id"]:  # saved before songs were recorded, or nothing was played: the library's confident top match
            # ponytail: one search query per row (~2 ms each, ≤ 20 rows); batch it if the list grows
            top = await search_songs(pool, p["id"], normalize_query(r["query"]))
            track_id = top[0][0] if top and top[0][1] >= config.SEARCH_HIT_THRESHOLD else None
        picks.append((r, track_id))
    tracks = {t["id"]: t for t in await pool.fetch(SEARCH_ART_SQL, [tid for _, tid in picks if tid])}
    out = []
    for r, tid in picks:
        t = tracks.get(tid)
        vid = r["video_id"] or (t and t["video_id"])
        # our own artwork when the track has some, else YouTube's thumbnail of the video, else the client shows its icon
        art = f"/api/art/track/{tid}" if t and t["has_art"] else f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else None
        out.append({"query": r["query"], "searched_at": r["searched_at"], "art_url": art})
    return out


@router.post("/me/searches", status_code=204)
async def add_search(body: SearchIn, request: Request, p=Depends(current_profile)):
    """Recorded by the client when a result is opened or played. Typing the same words again just moves them to the top."""
    q = " ".join(body.query.split())
    norm = normalize(q)
    if not norm:
        raise HTTPException(422, "Nothing to remember")
    async with request.app.state.pool.acquire() as c, c.transaction():
        await c.execute("INSERT INTO recent_searches (profile_id, query_norm, query, video_id) VALUES ($1, $2, $3, $4) "
                        "ON CONFLICT (profile_id, query_norm) DO UPDATE SET query = $3, searched_at = now(), "
                        "video_id = coalesce($4, recent_searches.video_id)", p["id"], norm, q, body.video_id)
        await c.execute("DELETE FROM recent_searches WHERE profile_id = $1 AND query_norm NOT IN "
                        "(SELECT query_norm FROM recent_searches WHERE profile_id = $1 ORDER BY searched_at DESC LIMIT $2)", p["id"], KEEP_SEARCHES)
    return Response(status_code=204)


@router.delete("/me/searches", status_code=204)
async def clear_searches(request: Request, query: str | None = None, p=Depends(current_profile)):
    """No `query`: clear everything. With `query`: remove that one entry."""
    pool = request.app.state.pool
    if query is None:
        await pool.execute("DELETE FROM recent_searches WHERE profile_id = $1", p["id"])
    else:
        await pool.execute("DELETE FROM recent_searches WHERE profile_id = $1 AND query_norm = $2", p["id"], normalize(query))
    return Response(status_code=204)


# --- offline downloads (M4.2-B): which playlists a device keeps. The device stores the audio; the server pins the files. ---

async def _own_device(pool, device_id: str, profile_id: int) -> None:
    if not await pool.fetchval("SELECT 1 FROM devices WHERE id = $1 AND profile_id = $2", device_id, profile_id):
        raise HTTPException(404, "Unknown device (connect to /ws once so it is registered)")


@router.put("/me/downloads/{playlist_id}", status_code=204)
async def mark_offline(playlist_id: int, request: Request, device: str = Query(min_length=1, max_length=64), p=Depends(current_profile)):
    s = request.app.state
    await _own_device(s.pool, device, p["id"])
    await owned(s.pool, playlist_id, p["id"])
    await s.pool.execute("INSERT INTO device_downloads (device_id, playlist_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", device, playlist_id)
    ids = [r["track_id"] for r in await s.pool.fetch("SELECT DISTINCT track_id FROM playlist_tracks WHERE playlist_id = $1", playlist_id)]
    await request_downloads_for_tracks(s.pool, s.redis, s.arq, ids, "low")  # the server needs the files before the device can fetch them
    return Response(status_code=204)


@router.delete("/me/downloads/{playlist_id}", status_code=204)
async def unmark_offline(playlist_id: int, request: Request, device: str = Query(min_length=1, max_length=64), p=Depends(current_profile)):
    await request.app.state.pool.execute(
        "DELETE FROM device_downloads dd USING devices d WHERE d.id = dd.device_id AND d.profile_id = $1 AND dd.device_id = $2 AND dd.playlist_id = $3",
        p["id"], device, playlist_id)
    return Response(status_code=204)


@router.get("/me/downloads")
async def list_offline(request: Request, device: str = Query(min_length=1, max_length=64), p=Depends(current_profile)):
    pool = request.app.state.pool
    await _own_device(pool, device, p["id"])
    rows = await pool.fetch(PLAYLIST_SELECT.replace("FROM playlists p", ", dd.created_at AS marked_at FROM playlists p JOIN device_downloads dd ON dd.playlist_id = p.id")
                            + " WHERE dd.device_id = $2 AND p.owner_id = $1 ORDER BY dd.created_at DESC", p["id"], device)
    return [{**playlist_json(r), "marked_at": r["marked_at"]} for r in rows]

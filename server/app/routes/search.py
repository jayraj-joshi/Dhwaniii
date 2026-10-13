"""Local search and suggest (planning.md §9.3, D22–D24, D26)."""
from fastapi import APIRouter, Depends, Query, Request

from .. import config, youtube
from ..text import normalize_query
from ..tracks import get_tracks
from . import discovery
from .playlists import PLAYLIST_SELECT, playlist_json
from .profiles import current_profile

router = APIRouter(prefix="/api")

# Candidates: any word-level full-text match, a close title, or the query as a word inside "artist title".
# Components are each 0-1 (D24): score = 0.55*fts + 0.35*trgm + 0.10*personal.
W_FTS, W_TRGM, W_PERSONAL = 0.55, 0.35, 0.10
SONGS_SQL = f"""
SELECT t.id,
    {W_FTS} * (t.search_vec @@ plainto_tsquery('simple', $1))::int
  + {W_TRGM} * greatest(similarity(t.title_norm, $1), similarity(t.artist_title_norm, $1),
                        word_similarity($1, t.artist_title_norm || ' ' || t.album_title_norm))
  + {W_PERSONAL} * least(1, coalesce(s.play_count, 0) / 20.0 + CASE WHEN l.track_id IS NULL THEN 0 ELSE 0.5 END) AS score
FROM tracks t
LEFT JOIN user_track_stats s ON s.track_id = t.id AND s.profile_id = $2
LEFT JOIN likes l ON l.track_id = t.id AND l.profile_id = $2
WHERE t.search_vec @@ plainto_tsquery('simple', $1) OR t.title_norm % $1 OR $1 <% t.artist_title_norm
ORDER BY score DESC, t.id
LIMIT 30
"""

ARTISTS_SQL = """
SELECT id, name, name_norm = $1 AS exact FROM artists
WHERE name_norm % $1 OR name_norm LIKE $1 || '%'
ORDER BY exact DESC, similarity(name_norm, $1) DESC, id LIMIT 20
"""

ALBUMS_SQL = """
SELECT al.id, al.title, al.year, ar.id AS artist_id, ar.name AS artist_name
FROM albums al LEFT JOIN artists ar ON ar.id = al.artist_id
WHERE al.title_norm % $1 OR al.title_norm LIKE $1 || '%'
ORDER BY similarity(al.title_norm, $1) DESC, al.id LIMIT 20
"""

PLAYLISTS_SQL = PLAYLIST_SELECT + """
WHERE p.owner_id = $2 AND (p.name_norm % $1 OR p.name_norm LIKE $1 || '%')
ORDER BY similarity(p.name_norm, $1) DESC, p.id LIMIT 20
"""


async def search_songs(db, profile_id: int, nq: str) -> list[tuple[int, float]]:
    return [(r["id"], r["score"]) for r in await db.fetch(SONGS_SQL, nq, profile_id)]


def artist_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "art_url": f"/api/art/artist/{r['id']}"}


def album_json(r) -> dict:
    artist = {"id": r["artist_id"], "name": r["artist_name"]} if r["artist_id"] else None
    return {"id": r["id"], "title": r["title"], "artist": artist, "year": r["year"], "art_url": f"/api/art/album/{r['id']}"}


@router.get("/search")
async def search(request: Request, q: str, type: str = Query("all", pattern="^(all|songs|artists|albums|playlists)$"),
                 remote: str = Query("auto", pattern="^(auto|always|never)$"), p=Depends(current_profile)):
    db, pid, nq = request.app.state.pool, p["id"], normalize_query(q)
    out = {"query_norm": nq, "cache_hit": False, "top": None, "songs": [], "artists": [], "albums": [],
           "playlists": [], "youtube": [], "youtube_artists": [], "youtube_albums": []}
    if not nq:
        return out
    want = lambda k: type in ("all", k)
    scored = await search_songs(db, pid, nq) if want("songs") else []
    out["songs"] = await get_tracks(db, pid, [i for i, _ in scored])
    out["cache_hit"] = bool(scored) and scored[0][1] >= config.SEARCH_HIT_THRESHOLD
    artist_rows = await db.fetch(ARTISTS_SQL, nq) if want("artists") or type == "all" else []
    if want("artists"):
        out["artists"] = [artist_json(r) for r in artist_rows]
    if want("albums"):
        out["albums"] = [album_json(r) for r in await db.fetch(ALBUMS_SQL, nq)]
    if want("playlists"):
        out["playlists"] = [playlist_json(r) for r in await db.fetch(PLAYLISTS_SQL, nq, pid)]
    for kind in ("artists", "albums"):  # the artist/album tabs list YouTube Music too, when the library has nothing or the user asks (D25)
        if type == kind and remote != "never" and (remote == "always" or not out[kind]):
            out[f"youtube_{kind}"] = await discovery.remote_kind(request, nq, kind)
    if type in ("all", "songs") and remote != "never" and (remote == "always" or not out["cache_hit"]):
        out["youtube"] = await remote_results(request, nq, remote == "always", {s["id"] for s in out["songs"]})
    if type == "all":
        if artist_rows and artist_rows[0]["exact"]:
            out["top"] = {"kind": "artist", "item": artist_json(artist_rows[0])}
        elif out["songs"]:
            out["top"] = {"kind": "song", "item": out["songs"][0]}
    return out


async def remote_results(request: Request, nq: str, merge: bool, local_track_ids: set[int]) -> list[dict]:
    ranked = await youtube.search(request.app.state.redis, nq, merge)
    rows = await request.app.state.pool.fetch(
        "SELECT video_id, track_id FROM sources WHERE video_id = ANY($1::text[])", [c["video_id"] for c in ranked])
    known = {r["video_id"]: r["track_id"] for r in rows}
    return [youtube.remote_json(c, c["video_id"] in known, known.get(c["video_id"])) for c in ranked
            if known.get(c["video_id"]) not in local_track_ids]  # a song already listed above is not repeated


SUGGEST_SQL = """
(SELECT 'track' AS kind, t.id, t.title AS label, ar.name AS sublabel, 0 AS rank
   FROM tracks t JOIN artists ar ON ar.id = t.primary_artist_id
   LEFT JOIN user_track_stats s ON s.track_id = t.id AND s.profile_id = $2
  WHERE t.title_norm LIKE $1 || '%' OR t.artist_title_norm LIKE $1 || '%'
  ORDER BY coalesce(s.play_count, 0) DESC, length(t.title_norm), t.id LIMIT 5)
UNION ALL
(SELECT 'artist', id, name, 'Artist', 1 FROM artists WHERE name_norm LIKE $1 || '%'
  ORDER BY length(name_norm), id LIMIT 2)
UNION ALL
(SELECT 'album', al.id, al.title, ar.name, 2 FROM albums al LEFT JOIN artists ar ON ar.id = al.artist_id
  WHERE al.title_norm LIKE $1 || '%' ORDER BY length(al.title_norm), al.id LIMIT 2)
ORDER BY rank LIMIT 8
"""


@router.get("/suggest")
async def suggest(request: Request, q: str, p=Depends(current_profile)):
    nq = normalize_query(q)
    if len(nq) < 2:
        return []
    rows = await request.app.state.pool.fetch(SUGGEST_SQL, nq, p["id"])
    return [{"kind": r["kind"], "id": r["id"], "label": r["label"], "sublabel": r["sublabel"]} for r in rows]


@router.get("/suggest/queries")
async def suggest_queries(request: Request, q: str, p=Depends(current_profile)):
    """Search completions from YouTube Music, for songs not in the library yet. A separate call so local suggest stays instant."""
    nq = normalize_query(q)
    return (await youtube.suggest_queries(request.app.state.redis, nq))[:6] if len(nq) >= 2 else []

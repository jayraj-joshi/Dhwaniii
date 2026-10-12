"""Home feed (M3.3-B, doc §9.2 and §10). Every section is built from data you can point at; an empty one is left out."""
from fastapi import APIRouter, Depends, Request

from ..tracks import get_tracks
from .library import recent_contexts
from .profiles import current_profile

router = APIRouter(prefix="/api")
ON_REPEAT_DAYS = 30
SECTION_SIZE = 12

# plays that counted (D38) in the last 30 days, per track
ON_REPEAT_SQL = f"""
SELECT h.track_id, count(*) AS plays FROM play_history h JOIN tracks t ON t.id = h.track_id
WHERE h.profile_id = $1 AND h.played_at > now() - interval '{ON_REPEAT_DAYS} days'
  AND h.ms_played >= CASE WHEN t.duration_ms IS NULL THEN 30000 ELSE least(30000, t.duration_ms / 2.0) END
GROUP BY h.track_id ORDER BY plays DESC, max(h.played_at) DESC, h.track_id LIMIT $2
"""

RECENTLY_ADDED_SQL = """
SELECT s.track_id FROM files f JOIN sources s ON s.id = f.source_id ORDER BY f.created_at DESC, f.source_id DESC LIMIT $1
"""


@router.get("/home")
async def home(request: Request, p=Depends(current_profile)):
    pool, pid = request.app.state.pool, p["id"]
    sections: list[dict] = []

    newest_like = await pool.fetchval("SELECT track_id FROM likes WHERE profile_id = $1 ORDER BY created_at DESC, track_id DESC LIMIT 1", pid)
    liked = {"context": "liked", "kind": "liked", "id": None, "name": "Liked Songs", "last_played": None,
             "art_url": f"/api/art/track/{newest_like}" if newest_like else None}
    quick = [liked, *await recent_contexts(pool, pid, 5, skip_liked=True)]
    sections.append({"id": "quick_access", "title": "Quick access", "items": quick})  # the Liked Songs tile is always there

    if jump := await recent_contexts(pool, pid, SECTION_SIZE):
        sections.append({"id": "jump_back_in", "title": "Jump back in", "items": jump})

    top = [r["track_id"] for r in await pool.fetch(ON_REPEAT_SQL, pid, SECTION_SIZE)]
    if top:
        tracks = await get_tracks(pool, pid, top)
        sections.append({"id": "on_repeat", "title": "On repeat", "items": tracks})

    if added := [r["track_id"] for r in await pool.fetch(RECENTLY_ADDED_SQL, SECTION_SIZE)]:
        sections.append({"id": "recently_added", "title": "Recently added", "items": await get_tracks(pool, pid, added)})
    return {"sections": sections}

"""Listening stats and yearly Wrapped (M5.5-B, D44). Everything is computed from this profile's play_history; nothing is stored.

A play *counts* by the D38 rule (30 s, or half of a shorter track). Minutes listened are real listening time: every reported
play contributes its ms_played, counted or not. Dates are UTC."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..tracks import get_tracks
from .profiles import current_profile
from .search import artist_json

router = APIRouter(prefix="/api")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
END_OF_TIME = datetime(9999, 1, 1, tzinfo=timezone.utc)
RANGES = {"4w": timedelta(weeks=4), "6m": timedelta(days=182), "1y": timedelta(days=365), "all": None}

# $1 profile, $2 window start (inclusive), $3 window end (exclusive)
WINDOW = """
WITH p AS (
  SELECT h.track_id, h.ms_played, h.skipped, h.played_at,
         h.ms_played >= CASE WHEN t.duration_ms IS NULL THEN 30000 ELSE least(30000, t.duration_ms / 2.0) END AS counted
  FROM play_history h JOIN tracks t ON t.id = h.track_id
  WHERE h.profile_id = $1 AND h.played_at >= $2 AND h.played_at < $3)
"""


def minutes(ms) -> float:
    return round((ms or 0) / 60_000, 1)


async def summary(pool, pid: int, start: datetime, end: datetime, top: int) -> dict:
    """Totals plus the top tracks and artists inside [start, end)."""
    t = await pool.fetchrow(WINDOW + "SELECT count(*) FILTER (WHERE counted) AS plays, count(*) FILTER (WHERE skipped) AS skips, "
                            "coalesce(sum(ms_played), 0) AS ms, count(DISTINCT (played_at AT TIME ZONE 'UTC')::date) AS days FROM p", pid, start, end)
    tracks = await pool.fetch(WINDOW + "SELECT track_id, count(*) FILTER (WHERE counted) AS plays, sum(ms_played) AS ms FROM p GROUP BY track_id "
                              "HAVING count(*) FILTER (WHERE counted) > 0 ORDER BY plays DESC, ms DESC, max(played_at) DESC, track_id LIMIT $4", pid, start, end, top)
    artists = await pool.fetch(WINDOW + "SELECT a.id, a.name, count(*) FILTER (WHERE p.counted) AS plays, sum(p.ms_played) AS ms FROM p "
                               "JOIN track_artists ta ON ta.track_id = p.track_id AND ta.role IN ('primary', 'featured') JOIN artists a ON a.id = ta.artist_id "
                               "GROUP BY a.id, a.name HAVING count(*) FILTER (WHERE p.counted) > 0 ORDER BY plays DESC, ms DESC, max(p.played_at) DESC, a.id LIMIT $4",
                               pid, start, end, top)
    objs = {x["id"]: x for x in await get_tracks(pool, pid, [r["track_id"] for r in tracks])}
    return {"plays": t["plays"], "skips": t["skips"], "minutes_listened": minutes(t["ms"]), "days_listened": t["days"],
            "top_tracks": [{"track": objs[r["track_id"]], "plays": r["plays"], "minutes": minutes(r["ms"])} for r in tracks if r["track_id"] in objs],
            "top_artists": [{"artist": artist_json(r), "plays": r["plays"], "minutes": minutes(r["ms"])} for r in artists]}


@router.get("/me/stats")
async def stats(request: Request, range: Annotated[str, Query(pattern="^(4w|6m|1y|all)$")] = "4w", p=Depends(current_profile)):
    span = RANGES[range]
    now = datetime.now(timezone.utc)
    start = now - span if span else EPOCH
    return {"range": range, "since": start if span else None, **await summary(request.app.state.pool, p["id"], start, END_OF_TIME, 10)}


def longest_streak(days: list[date]) -> int:
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return best


@router.get("/me/wrapped")
async def wrapped(request: Request, year: Annotated[int | None, Query(ge=2000, le=2100)] = None, p=Depends(current_profile)):
    """The year in numbers, as an ordered list of story cards for the client to step through. `cards` is empty if nothing was played."""
    pool, pid = request.app.state.pool, p["id"]
    year = year or datetime.now(timezone.utc).year
    start, end = datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    s = await summary(pool, pid, start, end, 5)
    out = {"year": year, "plays": s["plays"], "minutes_listened": s["minutes_listened"], "cards": []}
    if s["plays"] == 0:
        return out
    days = [r["d"] for r in await pool.fetch(WINDOW + "SELECT DISTINCT (played_at AT TIME ZONE 'UTC')::date AS d FROM p ORDER BY d", pid, start, end)]
    busiest = await pool.fetchrow(WINDOW + "SELECT (played_at AT TIME ZONE 'UTC')::date AS d, sum(ms_played) AS ms FROM p GROUP BY 1 ORDER BY ms DESC, d LIMIT 1", pid, start, end)
    album = await pool.fetchrow(
        WINDOW + "SELECT al.id, al.title, ar.name AS artist, count(*) FILTER (WHERE p.counted) AS plays FROM p JOIN tracks t ON t.id = p.track_id "
        "JOIN albums al ON al.id = t.album_id LEFT JOIN artists ar ON ar.id = al.artist_id GROUP BY al.id, al.title, ar.name "
        "HAVING count(*) FILTER (WHERE p.counted) > 0 ORDER BY plays DESC, al.id LIMIT 1", pid, start, end)
    new_artists = await pool.fetchval(
        "SELECT count(*) FROM (SELECT ta.artist_id, min(h.played_at) AS first FROM play_history h JOIN tracks t ON t.id = h.track_id "
        "JOIN track_artists ta ON ta.track_id = h.track_id AND ta.role IN ('primary', 'featured') WHERE h.profile_id = $1 "
        "AND h.ms_played >= CASE WHEN t.duration_ms IS NULL THEN 30000 ELSE least(30000, t.duration_ms / 2.0) END GROUP BY ta.artist_id) x "
        "WHERE x.first >= $2 AND x.first < $3", pid, start, end)
    days_in_year = (end - start).days
    cards = [
        {"id": "minutes", "title": f"Your {year} in music", "minutes_listened": s["minutes_listened"], "plays": s["plays"], "skips": s["skips"],
         "days_listened": s["days_listened"], "longest_streak_days": longest_streak(days),
         "minutes_per_active_day": round(s["minutes_listened"] / s["days_listened"], 1) if s["days_listened"] else 0, "share_of_year": round(s["days_listened"] / days_in_year, 3)},
        {"id": "top_song", "title": "Your top song", **s["top_tracks"][0]},
        {"id": "top_songs", "title": "Your top songs", "items": s["top_tracks"]},
        {"id": "top_artist", "title": "Your top artist", **s["top_artists"][0]},
        {"id": "top_artists", "title": "Your top artists", "items": s["top_artists"]},
    ]
    if album:
        cards.append({"id": "top_album", "title": "Your top album", "album": {"id": album["id"], "title": album["title"], "artist": album["artist"],
                                                                                 "art_url": f"/api/art/album/{album['id']}"}, "plays": album["plays"]})
    cards.append({"id": "busiest_day", "title": "Your biggest listening day", "date": busiest["d"].isoformat(), "minutes": minutes(busiest["ms"])})
    cards.append({"id": "discoveries", "title": "New artists you found", "count": new_artists})
    return {**out, "cards": cards}

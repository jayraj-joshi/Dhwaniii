"""Playlist import (M3.4-B, D41): YouTube / YouTube Music playlist URLs and CSV files (Exportify or generic columns)."""
import asyncio
import csv
import io
import logging
import os
import re
from urllib.parse import parse_qs, urlsplit

import yt_dlp

from . import config, enrich, youtube
from .text import normalize
from .tracks import ensure_track, request_downloads_for_tracks
from .ws import publish_profile

log = logging.getLogger("resonance.import")
LIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
MAX_ROWS = 5000
MAX_ENTRIES = 1000
SEARCH_PAUSE = 0.5  # seconds between ytmusicapi searches, to be gentle (M3.4)

COLUMNS = {  # canonical field -> accepted header names (lower case)
    "title": ("track name", "title", "track", "name", "song"),
    "artist": ("artist name(s)", "artist name", "artists", "artist"),
    "album": ("album name", "album"),
    "duration_ms": ("duration (ms)", "duration_ms", "duration"),
}


def playlist_id_from_url(url: str) -> str | None:
    """The `list=` id of a YouTube / YouTube Music URL, or None. Only this id is ever handed to yt-dlp, never the user's URL."""
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in HOSTS:
        return None
    ids = parse_qs(u.query).get("list", [])
    return ids[0] if ids and LIST_ID.match(ids[0]) else None


def parse_csv(data: bytes) -> list[dict]:
    """Rows as {title, artist, album, duration_ms}. Raises ValueError for anything that isn't a usable CSV."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("The file must be UTF-8 text")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The CSV has no header row")
    header = {h.strip().lower(): h for h in reader.fieldnames if h}
    pick = {f: next((header[n] for n in names if n in header), None) for f, names in COLUMNS.items()}
    if pick["title"] is None:
        raise ValueError("No title column: expected 'Track Name' or 'title'")
    rows = []
    for r in reader:
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"At most {MAX_ROWS} rows")
        get = lambda f: (r.get(pick[f]) or "").strip() if pick[f] else ""
        dur = get("duration_ms")
        rows.append({"title": get("title"), "artist": re.split(r"[;,]", get("artist"))[0].strip(), "album": get("album") or None,
                     "duration_ms": int(float(dur)) if re.fullmatch(r"\d+(\.\d+)?", dur) else None})
    if not rows:
        raise ValueError("The CSV has no rows")
    return rows


def _yt_playlist(list_id: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True, **(
            {"cookiefile": config.YTDLP_COOKIES_FILE} if os.path.exists(config.YTDLP_COOKIES_FILE) else {})}) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)


async def _add(pool, playlist_id: int, profile_id: int, track_id: int) -> None:
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT 1 FROM playlists WHERE id = $1 FOR UPDATE", playlist_id)  # one writer at a time
        await c.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position, added_by) "
            "SELECT $1, $2, coalesce(max(position), 0) + 1, $3 FROM playlist_tracks WHERE playlist_id = $1", playlist_id, track_id, profile_id)
        await c.execute("UPDATE playlists SET updated_at = now() WHERE id = $1", playlist_id)


async def _match_csv_row(row: dict) -> dict | None:
    """The best YouTube Music song for a CSV row: within ±10 s of its length, a similar title, then the §9.4 ranking."""
    if not row["title"]:
        return None
    q = f"{row['artist']} {row['title']}".strip()
    cands = [c for c in await asyncio.to_thread(youtube._ytm_search, q) if enrich.is_match(c, row["title"], row["duration_ms"])]
    return youtube.rank_candidates(cands, q, row["duration_ms"])[0] if cands else None


async def run_import(ctx, import_id: int):
    """arq job. Walks the source in order, adds every match to the playlist, records what it could not match, reports progress."""
    pool, redis = ctx["pool"], ctx["redis"]
    imp = await pool.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if imp is None or imp["status"] != "running":
        return
    unmatched: list[dict] = []
    matched = done = 0
    total = imp["total"]

    async def progress(final: bool = False):
        await pool.execute("UPDATE imports SET total = $2, matched = $3, unmatched = $4 WHERE id = $1", import_id, total, matched, unmatched)
        await publish_profile(redis, imp["profile_id"], {"type": "import_progress", "import_id": import_id, "done": done, "total": total})

    try:
        if imp["kind"] == "youtube":
            data = await asyncio.to_thread(_yt_playlist, imp["source_ref"])
            entries = (data.get("entries") or [])[:MAX_ENTRIES]
            total = len(entries)
            if data.get("title"):  # an import started without a name takes the playlist's own
                await pool.execute("UPDATE playlists SET name = $2, name_norm = $3 WHERE id = $1 AND name = 'Imported playlist'",
                                   imp["playlist_id"], data["title"][:100], normalize(data["title"][:100]))
            await progress()
            for i, e in enumerate(entries, 1):
                vid = e.get("id")
                if not vid or not youtube.VIDEO_ID_RE.match(vid) or (e.get("title") or "").startswith(("[Private", "[Deleted")):
                    unmatched.append({"row": i, "title": e.get("title") or vid or "", "artist": e.get("channel") or e.get("uploader") or "", "reason": "unavailable"})
                else:
                    tid = await ensure_track(pool, {"video_id": vid, "title": e.get("title") or vid, "channel": e.get("channel") or e.get("uploader"),
                                                    "duration_ms": int(e["duration"] * 1000) if e.get("duration") else None})
                    await _add(pool, imp["playlist_id"], imp["profile_id"], tid)
                    await request_downloads_for_tracks(pool, redis, redis, [tid], "low")
                    matched += 1
                done = i
                await progress()
        else:
            rows = imp["payload"]
            total = len(rows)
            await progress()
            for i, row in enumerate(rows, 1):
                try:
                    m = await _match_csv_row(row)
                    reason = ("no title" if not row["title"] else "no match") if m is None else None
                except Exception as e:
                    m, reason = None, f"search failed: {e}"[:200]
                if m is None:
                    unmatched.append({"row": i, "title": row["title"], "artist": row["artist"], "reason": reason})
                else:
                    tid = await ensure_track(pool, {k: m[k] for k in ("video_id", "title", "artists", "album", "duration_ms")} | {"quality_score": m.get("quality_score", 0)})
                    await _add(pool, imp["playlist_id"], imp["profile_id"], tid)
                    await request_downloads_for_tracks(pool, redis, redis, [tid], "low")
                    matched += 1
                done = i
                await progress()
                await asyncio.sleep(SEARCH_PAUSE)
        await pool.execute("UPDATE imports SET status = 'done', finished_at = now(), payload = NULL WHERE id = $1", import_id)
    except Exception as e:
        log.exception("import %s failed", import_id)
        await pool.execute("UPDATE imports SET status = 'failed', error = $2, finished_at = now(), total = $3, matched = $4, unmatched = $5 WHERE id = $1",
                           import_id, str(e)[:500], total, matched, unmatched)
    await publish_profile(redis, imp["profile_id"], {"type": "import_progress", "import_id": import_id, "done": done, "total": total})

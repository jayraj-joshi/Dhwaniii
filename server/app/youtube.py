"""YouTube search (D25) and candidate ranking (§9.4). Blocking libraries run in a thread."""
import asyncio
import json
import logging
import math
import os
import re

import httpx
import yt_dlp
from ytmusicapi import YTMusic

from . import config
from .text import normalize, parse_title

log = logging.getLogger("resonance.youtube")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")  # D12
PENALTY_WORDS = {"live", "cover", "remix", "slowed", "reverb", "8d", "karaoke"}
YTM_TIMEOUT_S = 6
SUGGEST_TIMEOUT_S = 2  # suggestions are useless once the user has finished typing

_ytm: YTMusic | None = None


def _client() -> YTMusic:
    global _ytm
    _ytm = _ytm or YTMusic()
    return _ytm


def song_to_remote(r: dict) -> dict | None:
    """One ytmusicapi song (from a search, an artist page or an album) as a ranked-search-style candidate. None if it has no playable id."""
    vid = r.get("videoId")
    if not vid or not VIDEO_ID_RE.match(vid):
        return None
    album = r.get("album")
    if isinstance(album, str):  # album pages give the title as a plain string
        album = {"name": album, "id": None}
    thumbs = r.get("thumbnails") or []
    secs = r.get("duration_seconds")
    return {
        "video_id": vid, "title": r["title"], "from_ytm": True, "channel": None, "view_count": None,
        "artists": [{"name": a["name"], "ytm_channel_id": a.get("id")} for a in r.get("artists") or [] if a.get("name")],
        "album": {"title": album["name"], "ytm_browse_id": album.get("id")} if album and album.get("name") else None,
        "duration_ms": secs * 1000 if secs else None,
        "thumb_url": thumbs[-1]["url"] if thumbs else None,
    }


def _ytm_search(q: str) -> list[dict]:
    return [c for c in map(song_to_remote, _client().search(q, filter="songs")) if c]


def _ytdlp_search(q: str) -> list[dict]:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}) as ydl:
        entries = ydl.extract_info(f"ytsearch10:{q}", download=False).get("entries") or []
    out = []
    for e in entries:
        vid = e.get("id")
        if not vid or not VIDEO_ID_RE.match(vid):
            continue
        channel = e.get("channel") or e.get("uploader")
        parsed = parse_title({"title": e.get("title") or "", "channel": channel})
        # artists found in the title itself; the channel fallback would make "channel == artist" always true
        title_artists = parse_title({"title": e.get("title") or ""})["artists"]
        thumbs = e.get("thumbnails") or []
        out.append({
            "video_id": vid, "title": parsed["title"], "from_ytm": False, "channel": channel,
            "view_count": e.get("view_count"),
            "artists": [{"name": n, "ytm_channel_id": None} for n in parsed["artists"]], "album": None,
            "title_artists": title_artists,
            "duration_ms": int(e["duration"] * 1000) if e.get("duration") else None,
            "thumb_url": thumbs[-1]["url"] if thumbs else None,
        })
    return out


def rank_candidates(cands: list[dict], query: str, ref_duration_ms: int | None = None) -> list[dict]:
    """Stable sort, best first (§9.4). Sets `quality_score` on each candidate."""
    qwords = set(normalize(query).split())
    for c in cands:
        ch = c.get("channel") or ""
        named = c["artists"][0]["name"] if c.get("artists") else None
        if "title_artists" in c:
            named = c["title_artists"][0] if c["title_artists"] else None
        artist = normalize(named) if named else ""
        s = 0.0
        if c.get("from_ytm") or ch.endswith(" - Topic") or ch.upper().endswith("VEVO") or (artist and normalize(ch) == artist):
            s += 3.0
        if ref_duration_ms and c.get("duration_ms") and abs(c["duration_ms"] - ref_duration_ms) <= 10_000:
            s += 1.5
        if (set(normalize(c["title"]).split()) & PENALTY_WORDS) - qwords:
            s -= 2.0
        if (c.get("duration_ms") or 0) > 12 * 60_000:
            s -= 5.0
        s += 0.1 * math.log10((c.get("view_count") or 0) + 1)
        c["quality_score"] = round(s, 3)
    return sorted(cands, key=lambda c: -c["quality_score"])


async def search(redis, nq: str, merge: bool = False) -> list[dict]:
    """Ranked RemoteTracks (without in_library/track_id). ytmusicapi first, yt-dlp when it fails or is empty (D25).
    merge=True (the "More from YouTube" button) combines both lists."""
    key = f"ys:{'more' if merge else 'songs'}:{nq}"
    if cached := await redis.get(key):
        return json.loads(cached)
    ytm: list[dict] = []
    try:
        ytm = await asyncio.wait_for(asyncio.to_thread(_ytm_search, nq), YTM_TIMEOUT_S)
    except Exception as e:  # ytmusicapi is unofficial; any failure falls back
        log.warning("ytmusicapi search failed (%s): %s", nq, e)
    dl: list[dict] = []
    if merge or not ytm:
        try:
            dl = await asyncio.to_thread(_ytdlp_search, nq)
        except Exception as e:
            log.warning("yt-dlp search failed (%s): %s", nq, e)
    seen: set[str] = set()
    merged = [c for c in ytm + dl if not (c["video_id"] in seen or seen.add(c["video_id"]))]
    ref = ytm[0]["duration_ms"] if ytm else None
    ranked = rank_candidates(merged, nq, ref)
    if ranked:
        await redis.set(key, json.dumps(ranked), ex=config.SEARCH_CACHE_TTL)
    return ranked


def _ytm_suggest(q: str) -> list[str]:
    return [s for s in _client().get_search_suggestions(q) if isinstance(s, str)]


async def suggest_queries(redis, nq: str) -> list[str]:
    """YouTube Music's autocomplete for the search box, cached like search. [] when it is unreachable (not cached, so it retries)."""
    key = f"yq:{nq}"
    if (cached := await redis.get(key)) is not None:
        return json.loads(cached)
    try:
        out = await asyncio.wait_for(asyncio.to_thread(_ytm_suggest, nq), SUGGEST_TIMEOUT_S)
    except Exception as e:
        log.warning("ytmusicapi suggestions failed (%s): %s", nq, e)
        return []
    await redis.set(key, json.dumps(out), ex=config.SEARCH_CACHE_TTL)
    return out


def remote_json(c: dict, in_library: bool, track_id: int | None) -> dict:
    """RemoteTrack (§7.1) plus `channel` and `quality_score`, which ensure_track keeps on the source row."""
    return {"video_id": c["video_id"], "title": c["title"], "artists": c["artists"], "album": c["album"],
            "duration_ms": c["duration_ms"], "thumb_url": c["thumb_url"], "channel": c.get("channel"),
            "quality_score": c["quality_score"], "in_library": in_library, "track_id": track_id}


# --- stream URL resolution (D30) -------------------------------------------------

MIME = {"m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm", "mp3": "audio/mpeg", "opus": "audio/ogg"}
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio"
_resolving: dict[str, asyncio.Task] = {}  # one yt-dlp run per video even when several requests arrive together


# yt-dlp's wording varies ("Video unavailable", "This video is unavailable", ...). "Service Unavailable" (HTTP 503) must not match.
UNAVAILABLE_RE = re.compile(r"video (is )?(unavailable|not available)|private video|removed|not available in your country|been terminated", re.I)


class Unavailable(Exception):
    """The video can't be played at all (private, removed, region-locked)."""


def ytdlp_opts(**extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "format": AUDIO_FORMAT, **extra}
    if os.path.exists(config.YTDLP_COOKIES_FILE):  # D8: only if the file exists
        opts["cookiefile"] = config.YTDLP_COOKIES_FILE
    return opts


def _resolve(video_id: str) -> dict:
    try:
        with yt_dlp.YoutubeDL(ytdlp_opts(skip_download=True)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError as e:
        if UNAVAILABLE_RE.search(str(e)):
            raise Unavailable(str(e)) from e
        raise
    headers = {k: v for k, v in (info.get("http_headers") or {}).items() if k.lower() != "accept-encoding"}
    return {"url": info["url"], "http_headers": headers, "mime": MIME.get(info.get("ext"), "audio/mp4")}


async def _resolve_and_cache(redis, video_id: str) -> dict:
    try:
        info = await asyncio.to_thread(_resolve, video_id)
    except Unavailable:
        raise
    except Exception as e:
        if not config.RAPIDAPI_KEY:
            raise
        log.warning("yt-dlp could not resolve %s, trying RapidAPI: %s", video_id, str(e)[:200])
        info = {"url": (await rapidapi_mp3(redis, video_id))["link"], "http_headers": {}, "mime": "audio/mpeg"}
    await redis.set(f"su:{video_id}", json.dumps(info), ex=config.STREAM_URL_TTL)
    return info


async def resolve_audio_url(redis, video_id: str, fresh: bool = False) -> dict:
    """{url, http_headers, mime}, cached in Redis as su:<video_id>. Direct URLs never go in Postgres."""
    if not fresh and (cached := await redis.get(f"su:{video_id}")):
        return json.loads(cached)
    task = _resolving.get(video_id)
    if task is None:
        task = _resolving[video_id] = asyncio.create_task(_resolve_and_cache(redis, video_id))
        task.add_done_callback(lambda _: _resolving.pop(video_id, None))
    return await asyncio.shield(task)


# --- RapidAPI fallback when yt-dlp is blocked ---------------------------------------

RAPIDAPI_HOST = "youtube-mp36.p.rapidapi.com"


async def rapidapi_mp3(redis, video_id: str, polls: int = 10, wait_s: float = 3) -> dict:
    """{link, title, duration} of the MP3 RapidAPI made, cached in Redis as ra:<video_id> so playing live and then
    downloading costs one call (the free plan allows 300 a month). The link expires after about two hours."""
    if cached := await redis.get(f"ra:{video_id}"):
        return json.loads(cached)
    headers = {"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": config.RAPIDAPI_KEY}
    async with httpx.AsyncClient(timeout=30) as http:
        for _ in range(polls):  # a video nobody has asked for yet comes back "processing" first
            r = await http.get(f"https://{RAPIDAPI_HOST}/dl", params={"id": video_id}, headers=headers)
            r.raise_for_status()
            d = r.json()
            if d.get("status") == "ok" and d.get("link"):
                out = {"link": d["link"], "title": d.get("title"), "duration": d.get("duration")}
                await redis.set(f"ra:{video_id}", json.dumps(out), ex=3600)
                return out
            if d.get("status") != "processing":
                raise RuntimeError(f"RapidAPI: {d.get('msg') or d}")
            await asyncio.sleep(wait_s)
    raise RuntimeError(f"RapidAPI: still converting {video_id}")


async def warm_up() -> None:
    """Pay the one-off per-process costs at startup (YouTube Music client setup ~4 s, yt-dlp's first extraction ~3 s)
    so the first real search and first play are not the slow ones. Failures are harmless."""
    try:
        await asyncio.to_thread(_ytm_search, "warm up")
        await asyncio.to_thread(_resolve, "dQw4w9WgXcQ")
    except Exception as e:
        log.info("warm-up skipped: %s", e)

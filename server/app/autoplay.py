"""Autoplay (D51): more songs for a radio-style list, found from web recommendations.

Flow: the song that is playing (from the second batch on: three random songs of the previous batch) -> the profile's method lists titles:
  tavily: ONE Tavily web search "<title> <artist>, ... similar songs" -> an LLM (NVIDIA) reads the results
  gemini: ONE Gemini call that names similar songs from what the model knows (no web search)
-> each title is resolved to a YouTube Music song -> the songs are saved as tracks and published to the profile as `autoplay_tracks`.
Every failure ends the same way (an empty event, no exception, no retry), so a missing key or an outage never spends credits twice.
"""
import asyncio
import json
import logging
import random
import re

import httpx

from . import config, youtube
from .text import normalize, normalize_query
from .tracks import ensure_track, get_tracks
from .ws import publish_profile

log = logging.getLogger("resonance.autoplay")
TAVILY_URL = "https://api.tavily.com/search"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TITLES_TTL = 7 * 24 * 3600
MAX_TITLES = 10
MAX_DURATION_MS = 10 * 60 * 1000
PARALLEL = 4
HISTORY_EXCLUDE = 50
EXAMPLES = 3          # songs of the previous batch that go into the query, so the search sees more than one example
BATCH_TTL = 6 * 3600  # how long the id list of the last published batch is remembered


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))  # each call below passes its own timeout


async def tavily(client: httpx.AsyncClient, query: str) -> list[dict]:
    r = await client.post(TAVILY_URL, timeout=25, headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                          json={"query": query, "search_depth": "advanced", "max_results": 10, "include_raw_content": "text"})
    r.raise_for_status()
    return r.json().get("results") or []


def quote_list(titles: list[str]) -> str:
    q = [f'"{t}"' for t in titles]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + f" and {q[-1]}"


def build_prompt(titles: list[str] | str, results: list[dict]) -> str:
    titles = [titles] if isinstance(titles, str) else titles
    q = quote_list(titles)
    skip = f"Do not include {q} itself." if len(titles) == 1 else "Do not include any of those songs themselves."
    text = "\n\n---\n\n".join(f"{r.get('title') or ''}\n{r.get('content') or ''}\n{(r.get('raw_content') or '')[:3000]}" for r in results)
    return (f"Below are web search results about songs similar to {q}. From this text, list up to {MAX_TITLES} song titles that are "
            f"recommended as similar to {q}. {skip} Titles only, no artists, no numbering. "
            f'Reply with only a JSON array of strings, like ["Title 1", "Title 2"].\n\n{text}')


async def llm(client: httpx.AsyncClient, prompt: str) -> str:
    r = await client.post(NVIDIA_URL, timeout=30, headers={"Authorization": f"Bearer {config.NVIDIA_API_KEY}"},
                          json={"model": config.NVIDIA_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0,
                                "max_tokens": 300, "chat_template_kwargs": {"enable_thinking": False}})
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def gemini_prompt(examples: list[tuple[int, str, str]]) -> str:
    songs = [f'"{t}" by {a}' for _, t, a in examples]
    q = songs[0] if len(songs) == 1 else ", ".join(songs[:-1]) + f" and {songs[-1]}"
    skip = "that song itself" if len(examples) == 1 else "any of those songs themselves"
    return (f"List up to {MAX_TITLES} real, released songs that a listener who likes {q} would enjoy next: similar in language, mood "
            f"and style, by a mix of artists. Do not include {skip}. Write each as \"Title - Artist\", no numbering. "
            f'Reply with only a JSON array of strings, like ["Title 1 - Artist 1", "Title 2 - Artist 2"].')


async def gemini(client: httpx.AsyncClient, prompt: str) -> str:
    """GEMINI_MODEL may list several models, newest first: a busy one (503 high demand, 429 quota) hands over to the next."""
    for model in [m.strip() for m in config.GEMINI_MODEL.split(",") if m.strip()]:
        r = await client.post(GEMINI_URL.format(model=model), timeout=30, headers={"x-goog-api-key": config.GEMINI_API_KEY},
                              json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                    "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048}})  # thinking tokens count too
        if r.status_code not in (429, 503):
            break
        log.info("gemini model %s is busy (%s), trying the next", model, r.status_code)
    r.raise_for_status()
    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def parse_titles(reply: str, drop: list[str] | str) -> list[str]:
    """Song titles from the model's reply: a JSON array (possibly fenced or surrounded by prose), else the quoted strings, which
    still works when the reply was cut off at the token limit. The example songs themselves (and anything containing their titles) are dropped."""
    titles = None
    if m := re.search(r"\[.*\]", reply, re.S):
        try:
            titles = [t for t in json.loads(m.group(0)) if isinstance(t, str)]
        except ValueError:
            pass
    if titles is None:
        titles = re.findall(r'"([^"\n]+)"', reply)
    drops = [d for d in (normalize(x) for x in ([drop] if isinstance(drop, str) else drop)) if d]
    seen, out = set(), []
    for t in titles:
        n = normalize(t)
        if n and n not in seen and not any(d in n for d in drops):
            seen.add(n)
            out.append(t.strip())
    return out[:MAX_TITLES]


def sample(population: list[int], k: int) -> list[int]:
    return random.sample(population, k)  # a function of its own so tests can make the choice deterministic


async def titles_for(redis, examples: list[tuple[int, str, str]], also_drop: list[str], method: str = "tavily") -> list[str]:
    """Recommended titles for the example songs [(track_id, title, artist), ...]: one search and/or one model call, however many examples."""
    ids = "+".join(str(i) for i in sorted(e[0] for e in examples))
    key = f"ap:titles:{ids}" if method == "tavily" else f"ap:titles:{method}:{ids}"  # each method keeps its own answers
    if hit := await redis.get(key):
        return json.loads(hit)
    names = [e[1] for e in examples]
    async with make_client() as c:
        if method == "gemini":
            reply = await gemini(c, gemini_prompt(examples))
        else:
            reply = await llm(c, build_prompt(names, await tavily(c, ", ".join(f"{t} {a}" for _, t, a in examples) + " similar songs")))
        titles = parse_titles(reply, names + also_drop)
    if titles:  # an empty answer is never cached: the next try may do better
        await redis.set(key, json.dumps(titles), ex=TITLES_TTL)
    return titles


def batch_key(profile_id: int, ctype: str, cid: str) -> str:
    return f"ap:batch:{profile_id}:{ctype}:{cid}"


async def previous_batch(redis, profile_id: int, ctype: str, cid: str, after_len: int) -> list[int] | None:
    """Ids of the last batch we published for this radio, if the list the client has is exactly that batch added on (otherwise the
    client dropped or never got it, and we only know the song that is playing)."""
    if ctype != "radio":
        return None
    raw = await redis.get(batch_key(profile_id, ctype, cid))
    rec = json.loads(raw) if raw else None
    return rec["ids"] if rec and rec.get("len") == after_len and rec.get("ids") else None


async def remember_batch(redis, profile_id: int, ctype: str, cid: str, seed_track_id: int, after_len: int, ids: list[int]) -> None:
    if not ids:
        return
    # a radio grows by the batch; a playlist/album/artist/liked batch becomes a NEW radio:<seed> that holds only these tracks (§10.3)
    ctx, new_len = (f"radio:{cid}", after_len + len(ids)) if ctype == "radio" else (f"radio:{seed_track_id}", len(ids))
    t, _, i = ctx.partition(":")
    await redis.set(batch_key(profile_id, t, i), json.dumps({"len": new_len, "ids": ids}), ex=BATCH_TTL)


async def resolve(pool, redis, titles: list[str], exclude: set[int]) -> tuple[list[int], dict]:
    """Track ids for the titles, in the model's order. Each title becomes its top YouTube Music result; results already played or
    listed, repeats, and anything over ten minutes are dropped."""
    sem = asyncio.Semaphore(PARALLEL)

    async def top(t: str):
        async with sem:
            try:
                cands = await youtube.search(redis, normalize_query(t))
            except Exception as e:
                log.info("could not resolve %r: %s", t, e)
                return None
        return cands[0] if cands else None

    found = await asyncio.gather(*(top(t) for t in titles))
    known = {r["video_id"]: r["track_id"] for r in await pool.fetch(
        "SELECT video_id, track_id FROM sources WHERE video_id = ANY($1::text[])", [c["video_id"] for c in found if c])}
    ids, seen_vids = [], set()
    for c in found:
        if c is None or c["video_id"] in seen_vids or (c.get("duration_ms") or 0) > MAX_DURATION_MS:
            continue
        seen_vids.add(c["video_id"])
        if known.get(c["video_id"]) in exclude:
            continue
        tid = await ensure_track(pool, {k: c.get(k) for k in ("video_id", "title", "artists", "album", "duration_ms", "channel", "quality_score")})
        if tid not in exclude and tid not in ids:
            ids.append(tid)
    return ids, {"titles": len(titles), "resolved": sum(1 for c in found if c), "kept": len(ids)}


async def autoplay_extend(ctx, profile_id: int, context_key: str, after_len: int, seed_track_id: int, exclude_track_ids: list[int],
                          method: str = "tavily"):
    """arq job. Always ends by publishing `autoplay_tracks` to the profile, empty on any failure, so the client can stop waiting."""
    pool, redis = ctx["pool"], ctx["redis"]
    ctype, _, cid = context_key.partition(":")
    tracks, stats = [], {}
    try:
        if method not in config.autoplay_methods():
            raise RuntimeError(f"autoplay method {method!r} has no API key set")
        seed = await pool.fetchrow("SELECT t.title, a.name AS artist FROM tracks t JOIN artists a ON a.id = t.primary_artist_id WHERE t.id = $1", seed_track_id)
        if seed is None:
            raise LookupError(f"seed track {seed_track_id} is gone")
        examples = [(seed_track_id, seed["title"], seed["artist"])]
        if prev := await previous_batch(redis, profile_id, ctype, cid, after_len):  # from the second batch on: three random songs of the last one
            rows = {r["id"]: r for r in await pool.fetch("SELECT t.id, t.title, a.name AS artist FROM tracks t JOIN artists a ON a.id = t.primary_artist_id WHERE t.id = ANY($1::bigint[])", prev)}
            picked = [rows[i] for i in sample(prev, min(EXAMPLES, len(prev))) if i in rows]
            examples = [(r["id"], r["title"], r["artist"]) for r in picked] or examples
        titles = await titles_for(redis, examples, [seed["title"]], method)
        ids, stats = await resolve(pool, redis, titles, {*exclude_track_ids, seed_track_id, *(e[0] for e in examples)})
        stats["examples"], stats["method"] = [e[0] for e in examples], method
        await remember_batch(redis, profile_id, ctype, cid, seed_track_id, after_len, ids)
        tracks = await get_tracks(pool, profile_id, ids)
    except Exception as e:  # no re-raise: arq would retry and spend credits again
        log.warning("autoplay for profile %s (%s) failed: %s", profile_id, context_key, e)
    await publish_profile(redis, profile_id, {"type": "autoplay_tracks", "context": {"type": ctype, "id": cid}, "after_len": after_len,
                                              "seed_track_id": seed_track_id, "tracks": tracks, "stats": stats})
    return {"tracks": len(tracks), **stats}

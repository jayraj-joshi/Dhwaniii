"""M1.8: download_track, retries, eviction, backlog. Only yt-dlp's network step is faked; ffmpeg, mutagen, SQL and Redis are real."""
import asyncio
import json
import shutil
import subprocess
import uuid

import asyncpg
import httpx
import pytest
from arq import Retry, create_pool
from arq.connections import RedisSettings
from mutagen.mp4 import MP4

from app import config, db, storage, worker, youtube
from tests.conftest import TEST_DB, _url


def run(coro_fn):
    async def go():
        c = await asyncpg.connect(_url(TEST_DB))
        try:
            return await coro_fn(c)
        finally:
            await c.close()
    return asyncio.run(go())


def vid():
    return "wk" + uuid.uuid4().hex[:9]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", "-b:a", "128k", str(d / "a.m4a")], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1", str(d / "t.jpg")], check=True)
    return d


@pytest.fixture
def env(client, tmp_path, monkeypatch, media):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "POLITE_SLEEP", (0, 0))
    calls = []

    def fake(info):
        def _dl(video_id, on_progress, overwrite=False):
            calls.append((video_id, overwrite) if overwrite else video_id)
            a = storage.abs_path(storage.audio_rel(video_id, "m4a"))
            a.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(media / "a.m4a", a)
            shutil.copy(media / "t.jpg", a.with_suffix(".jpg"))
            on_progress(50)
            return {"duration": 3, **info}
        monkeypatch.setattr(worker, "_download", _dl)
    return fake, calls, tmp_path


async def with_ctx(fn, job_try=1):
    pool = await db.create_pool()
    redis = await create_pool(RedisSettings.from_dsn(config.REDIS_URL))
    try:
        return await fn({"pool": pool, "redis": redis, "job_try": job_try})
    finally:
        await redis.aclose()
        await pool.close()


async def drain(ps):
    out = []
    while m := await ps.get_message(ignore_subscribe_messages=True, timeout=0.3):
        out.append(json.loads(m["data"]))
    return out


def ensure(client, v, title="Tum Hi Ho", artist="Arijit Singh"):
    h = {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"w-{uuid.uuid4().hex[:8]}"}).json()["id"])}
    t = client.post("/api/tracks/ensure", json=[{"video_id": v, "title": title, "artists": [{"name": artist}]}], headers=h).json()[0]
    return h, t


def test_full_pipeline_then_idempotent(client, env):
    fake, calls, root = env
    fake({"title": "Tum Hi Ho (Official Video)", "channel": "T-Series"})  # no music fields: the search hint's metadata must survive
    v = vid()
    h, t = ensure(client, v)

    async def go(ctx):
        ps = ctx["redis"].pubsub()
        await ps.subscribe("ws:all")
        await ps.get_message(timeout=1)
        await ctx["redis"].set(f"dl:lock:{v}", "1")
        await worker.download_track(ctx, v)
        msgs = await drain(ps)
        await worker.download_track(ctx, v)  # second call: nothing to do
        return msgs
    msgs = asyncio.run(with_ctx(go))

    f = run(lambda c: c.fetchrow("SELECT f.* FROM files f JOIN sources s ON s.id = f.source_id WHERE s.video_id = $1", v))
    audio = root / f"{v[:2]}/{v[2:4]}/{v}.m4a"
    assert f["path"] == f"{v[:2]}/{v[2:4]}/{v}.m4a" and f["codec"] == "m4a" and f["bytes"] == audio.stat().st_size
    assert f["bitrate"] and -20 <= f["gain_db"] <= 6 and f["loudness_lufs"] < 0
    tags = MP4(audio)
    assert tags["\xa9nam"] == ["Tum Hi Ho"] and tags["\xa9ART"] == ["Arijit Singh"] and tags["covr"]
    art = root / f"art/track/{v}.jpg"
    dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(art)], capture_output=True, text=True).stdout.strip()
    assert dims == "600,600" and not (root / f"{v[:2]}/{v[2:4]}/{v}.jpg").exists()
    types = [m["type"] for m in msgs]
    assert "download_progress" in types and {"type": "track_cached", "video_id": v, "track_id": t["id"]} in msgs
    job = run(lambda c: c.fetchrow("SELECT status, attempts, error FROM download_jobs WHERE video_id = $1", v))
    assert (job["status"], job["attempts"], job["error"]) == ("done", 1, None)
    assert calls == [v]  # the second call did not download again
    d = client.get(f"/api/tracks/{t['id']}", headers=h).json()
    assert d["cache_state"] == "cached" and d["title"] == "Tum Hi Ho" and d["gain_db"] == f["gain_db"]
    assert client.get(f"/api/art/track/{t['id']}").status_code == 200


def test_music_metadata_upgrades_provisional_but_not_manual(client, env):
    fake, calls, root = env
    fake({"title": "x", "track": "Kesariya", "artists": ["Arijit Singh", "Pritam"], "album": "Brahmastra", "release_year": 2022})
    v1, v2 = vid(), vid()
    h, t1 = ensure(client, v1, title="kesariya lyrical", artist="Guessed Artist")
    _, t2 = ensure(client, v2, title="kesariya lyrical", artist="Someone Else")
    run(lambda c: c.execute("UPDATE tracks SET metadata_status = 'manual' WHERE id = $1", t2["id"]))
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v1)))
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v2)))
    a = client.get(f"/api/tracks/{t1['id']}", headers=h).json()
    b = client.get(f"/api/tracks/{t2['id']}", headers=h).json()
    assert a["title"] == "Kesariya" and [x["name"] for x in a["artists"]] == ["Arijit Singh", "Pritam"] and a["album"]["title"] == "Brahmastra"
    assert b["title"] == "kesariya lyrical" and b["artists"][0]["name"] == "Someone Else"
    assert run(lambda c: c.fetchval("SELECT year FROM albums WHERE id = $1", a["album"]["id"])) == 2022
    assert run(lambda c: c.fetchval("SELECT count(*) FROM artists WHERE id = $1", t1["artists"][0]["id"])) == 0  # the guessed artist row was cleaned up
    assert run(lambda c: c.fetchval("SELECT count(*) FROM artists WHERE id = $1", t2["artists"][0]["id"])) == 1  # but not while the manual track still uses it


def test_download_without_prior_track_creates_one(client, env):
    fake, calls, root = env
    fake({"title": "Coldplay - Yellow (Official Video)", "channel": "Coldplay"})
    v = vid()
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v)))
    row = run(lambda c: c.fetchrow("SELECT t.title, a.name FROM sources s JOIN tracks t ON t.id = s.track_id JOIN artists a ON a.id = t.primary_artist_id WHERE s.video_id = $1", v))
    assert (row["title"], row["name"]) == ("Yellow", "Coldplay")


def boom(msg):
    def _dl(video_id, on_progress, overwrite=False):
        raise RuntimeError(msg)
    return _dl


@pytest.mark.parametrize("msg", ["ERROR: [youtube] x: Private video. Sign in if you've been granted access",
                                 "ERROR: [youtube] aaaaaaaaaaa: This video is unavailable",
                                 "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader"])
def test_permanent_error_fails_without_retry(client, env, monkeypatch, msg):
    v = vid()
    monkeypatch.setattr(worker, "_download", boom(msg))

    async def go(ctx):
        ps = ctx["redis"].pubsub(); await ps.subscribe("ws:all"); await ps.get_message(timeout=1)
        await ctx["redis"].set(f"dl:lock:{v}", "1")
        await worker.download_track(ctx, v)  # must not raise Retry
        return await drain(ps), await ctx["redis"].exists(f"dl:lock:{v}")
    msgs, lock = asyncio.run(with_ctx(go))
    job = run(lambda c: c.fetchrow("SELECT status, error FROM download_jobs WHERE video_id = $1", v))
    assert job["status"] == "failed" and job["error"] == msg[:500] and lock == 0
    assert any(m["type"] == "download_failed" and m["video_id"] == v for m in msgs)


def test_transient_error_backoff_then_final_failure(client, env, monkeypatch):
    v = vid()
    monkeypatch.setattr(worker, "_download", boom("HTTP Error 503: Service Unavailable"))
    for attempt, defer in [(1, 30), (2, 120), (3, 480)]:
        with pytest.raises(Retry) as e:
            asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v), job_try=attempt))
        assert e.value.defer_score == defer * 1000
        assert run(lambda c: c.fetchval("SELECT status FROM download_jobs WHERE video_id = $1", v)) == "queued"
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v), job_try=4))  # 4th attempt = last (DOWNLOAD_RETRIES + 1)
    job = run(lambda c: c.fetchrow("SELECT status, attempts, error FROM download_jobs WHERE video_id = $1", v))
    assert (job["status"], job["attempts"]) == ("failed", 4) and "503" in job["error"]


BOT = "ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."


@pytest.fixture
def rapid(monkeypatch, media):
    """A blocked yt-dlp and a fake RapidAPI download. Returns the video ids RapidAPI was asked for."""
    monkeypatch.setattr(config, "RAPIDAPI_KEY", "k")
    monkeypatch.setattr(worker, "_download", boom(BOT))
    used = []

    async def _rapid(redis, video_id):
        used.append(video_id)
        a = storage.abs_path(storage.audio_rel(video_id, "m4a"))
        a.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(media / "a.m4a", a)
        shutil.copy(media / "t.jpg", a.with_suffix(".jpg"))
        return {"title": "Coldplay - Yellow (Official Video)", "duration": 3.0, "rapidapi": True}
    monkeypatch.setattr(worker, "_download_rapidapi", _rapid)
    return used


def rapidapi_flag(v):
    return run(lambda c: c.fetchval("SELECT f.rapidapi FROM files f JOIN sources s ON s.id = f.source_id WHERE s.video_id = $1", v))


def test_rapidapi_fallback_when_ytdlp_is_blocked(client, env, rapid):
    _, _, root = env
    v = vid()
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v)))  # no Retry: the fallback made the file
    assert rapid == [v] and rapidapi_flag(v) is True
    assert run(lambda c: c.fetchval("SELECT status FROM download_jobs WHERE video_id = $1", v)) == "done"
    assert (root / f"art/track/{v}.jpg").exists()


def test_redownload_rapidapi_waits_for_youtube_then_replaces(client, env, rapid, monkeypatch):
    fake, calls, root = env
    v1, v2, gone = vid(), vid(), vid()
    for v in (v1, v2, gone):
        asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v)))
    run(lambda c: c.execute("UPDATE files f SET rapidapi = false FROM sources s WHERE s.id = f.source_id AND s.video_id <> ALL($1)", [v1, v2, gone]))

    res = asyncio.run(with_ctx(worker.redownload_rapidapi))  # YouTube still blocks: stop at the first, keep them all
    assert res == {"redownloaded": 0, "picked": 3} and all(rapidapi_flag(v) for v in (v1, v2, gone))

    fake({"title": "x", "track": "Yellow", "artists": ["Coldplay"], "album": "Parachutes"})
    ok = worker._download
    monkeypatch.setattr(worker, "_download", lambda v, p, overwrite=False: boom("ERROR: [youtube] x: Private video")(v, p) if v == gone else ok(v, p, overwrite))
    res = asyncio.run(with_ctx(worker.redownload_rapidapi))
    assert res == {"redownloaded": 2, "picked": 3} and calls == [(v1, True), (v2, True)]
    assert (rapidapi_flag(v1), rapidapi_flag(v2), rapidapi_flag(gone)) == (False, False, False)  # the gone one keeps its file, but is not retried
    assert storage.abs_path(storage.audio_rel(gone, "m4a")).exists()
    album = run(lambda c: c.fetchval("SELECT a.title FROM sources s JOIN tracks t ON t.id = s.track_id JOIN albums a ON a.id = t.album_id WHERE s.video_id = $1", v1))
    assert album == "Parachutes"
    assert asyncio.run(with_ctx(worker.redownload_rapidapi)) == {"redownloaded": 0, "picked": 0}


@pytest.mark.parametrize("key,msg", [("k", "ERROR: [youtube] x: Private video. Sign in if you've been granted access"), ("", BOT)])
def test_rapidapi_fallback_skipped(client, env, monkeypatch, key, msg):
    """Not for a video that is gone for good, and not without a key."""
    monkeypatch.setattr(config, "RAPIDAPI_KEY", key)
    monkeypatch.setattr(worker, "_download", boom(msg))
    used = []
    monkeypatch.setattr(worker, "_download_rapidapi", lambda redis, v: used.append(v))
    try:
        asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, vid())))
    except Retry:
        pass
    assert used == []


def test_rapidapi_fallback_failure_keeps_both_errors(client, env, monkeypatch):
    monkeypatch.setattr(config, "RAPIDAPI_KEY", "k")
    monkeypatch.setattr(worker, "_download", boom(BOT))

    async def _rapid(redis, video_id):
        raise RuntimeError("RapidAPI: quota exceeded")
    monkeypatch.setattr(worker, "_download_rapidapi", _rapid)
    v = vid()
    with pytest.raises(Retry):
        asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v)))
    err = run(lambda c: c.fetchval("SELECT error FROM download_jobs WHERE video_id = $1", v))
    assert "quota exceeded" in err and "not a bot" in err


def test_rapidapi_polls_until_ready_and_caches(client, monkeypatch):
    replies = iter([{"status": "processing", "msg": "in process"},
                    {"status": "ok", "link": "https://cdn.example/a.mp3", "title": "T", "duration": 3.0}])
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=next(replies))
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: real(transport=httpx.MockTransport(handler), **k))
    monkeypatch.setattr(config, "RAPIDAPI_KEY", "k")
    v = vid()

    async def go(ctx):
        return [await youtube.rapidapi_mp3(ctx["redis"], v, wait_s=0) for _ in range(2)]
    first, second = asyncio.run(with_ctx(go))
    assert first == second == {"link": "https://cdn.example/a.mp3", "title": "T", "duration": 3.0}
    assert len(seen) == 2 and seen[0].url.params["id"] == v and seen[0].headers["x-rapidapi-key"] == "k"  # the second call came from Redis


def test_eviction_keeps_liked_and_track_rows(client, env, monkeypatch):
    _, _, root = env
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", 3000 / storage.GB)  # 3000 bytes
    h, _ = ensure(client, vid())
    pid = int(h["X-Profile-Id"])
    vs = [vid() for _ in range(4)]

    async def seed(c):
        await c.execute("DELETE FROM files")
        ids = []
        for i, v in enumerate(vs):
            a = await c.fetchval("INSERT INTO artists (name, name_norm) VALUES ('E', $1) RETURNING id", v)
            t = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id) VALUES ('E', 'e', $1) RETURNING id", a)
            s = await c.fetchval("INSERT INTO sources (track_id, video_id) VALUES ($1, $2) RETURNING id", t, v)
            rel = f"ev/{v}.m4a"; (root / "ev").mkdir(exist_ok=True); (root / rel).write_bytes(b"x" * 1000)
            await c.execute("INSERT INTO files (source_id, path, codec, bytes, created_at) VALUES ($1, $2, 'm4a', 1000, now() - $3 * interval '1 hour')", s, rel, 10 - i)
            ids.append((s, t))
        await c.execute("INSERT INTO likes (profile_id, track_id) VALUES ($1, $2)", pid, ids[0][1])  # the oldest file is liked
        return ids
    ids = run(seed)
    res = asyncio.run(with_ctx(lambda ctx: worker.evict(ctx)))
    left = run(lambda c: c.fetch("SELECT s.video_id FROM files f JOIN sources s ON s.id = f.source_id ORDER BY f.created_at"))
    # 4000 bytes > 3000: evict oldest unpinned (vs[1], vs[2]) until <= 2700; vs[0] is liked, vs[3] is newest
    assert res["evicted"] == 2 and [r["video_id"] for r in left] == [vs[0], vs[3]]
    assert not (root / f"ev/{vs[1]}.m4a").exists() and (root / f"ev/{vs[0]}.m4a").exists()
    assert run(lambda c: c.fetchval("SELECT count(*) FROM tracks WHERE id = ANY($1)", [t for _, t in ids])) == 4
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", 1)
    assert asyncio.run(with_ctx(lambda ctx: worker.evict(ctx)))["evicted"] == 0


def test_feed_backlog_moves_low_priority_into_arq_without_duplicates(client, env, monkeypatch):
    monkeypatch.setattr(config, "WORKER_MAX_JOBS", 2)
    vs = [vid() for _ in range(5)]

    async def go(ctx):
        r = ctx["redis"]
        await r.delete("arq:queue")
        await ctx["pool"].execute("DELETE FROM download_jobs WHERE status = 'queued' AND priority = 'low'")
        for v in vs:
            await ctx["pool"].execute("INSERT INTO download_jobs (video_id, status, priority, created_at) VALUES ($1, 'queued', 'low', now() + $2 * interval '1 second')", v, vs.index(v))
        await worker.feed_backlog(ctx)
        first = {m.decode() for m in await r.zrange("arq:queue", 0, -1)}
        await worker.feed_backlog(ctx)  # queue already full: nothing more
        second = {m.decode() for m in await r.zrange("arq:queue", 0, -1)}
        await r.zrem("arq:queue", f"dl:{vs[0]}")  # one job picked up by a worker; the lock still guards it
        await worker.feed_backlog(ctx)
        third = {m.decode() for m in await r.zrange("arq:queue", 0, -1)}
        return first, second, third
    first, second, third = asyncio.run(with_ctx(go))
    assert first == {f"dl:{vs[0]}", f"dl:{vs[1]}"} and second == first
    assert third == {f"dl:{vs[1]}", f"dl:{vs[2]}"}  # skipped vs[0] (locked), took the next oldest

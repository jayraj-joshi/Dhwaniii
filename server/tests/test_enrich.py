"""M2.1: enrichment, merge_artists, and the worker hook. YouTube Music is faked; ffmpeg, mutagen and SQL are real."""
import asyncio
import shutil
import subprocess
import uuid

import pytest
from mutagen.mp4 import MP4

from app import config, db, enrich, storage, worker, youtube
from tests.helpers import mk_track, prof, run
from tests.test_worker import with_ctx, media  # noqa: F401  (media fixture: a real m4a and thumbnail)


@pytest.fixture
def cover_png(tmp_path_factory):
    d = tmp_path_factory.mktemp("cov")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=orange:s=900x600", "-frames:v", "1", str(d / "c.png")], check=True)
    return d / "c.png"


@pytest.fixture
def ytm(client, tmp_path, monkeypatch, cover_png):
    """Fake YouTube Music: `cands` is what a search returns, `albums` maps browse id -> get_album payload."""
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    state = {"cands": [], "albums": {}, "queries": []}
    monkeypatch.setattr(youtube, "_ytm_search", lambda q: (state["queries"].append(q), list(state["cands"]))[1])
    monkeypatch.setattr(enrich, "_get_album", lambda b: state["albums"][b])
    monkeypatch.setattr(enrich, "_fetch_cover", lambda url, dst: storage.crop_art(cover_png, dst))
    return state, tmp_path


def cand(title="Tum Hi Ho", artist="Arijit Singh", channel="UCarijit", album="Aashiqui 2", browse="MPREb_aashiqui", dur=262_000):
    return {"video_id": "x" * 11, "title": title, "artists": [{"name": artist, "ytm_channel_id": channel}], "from_ytm": True,
            "album": {"title": album, "ytm_browse_id": browse} if album else None, "duration_ms": dur, "thumb_url": None}


def row(sql, *a):
    return run(lambda c: c.fetchrow(sql, *a))


def ensure(client, h, title, artist, dur=262_000):
    vid = "e" + uuid.uuid4().hex[:10]
    return client.post("/api/tracks/ensure", json=[{"video_id": vid, "title": title, "artists": [{"name": artist}], "album": {"title": "Aashiqui 2 (Official)"},
                                                      "duration_ms": dur}], headers=h).json()[0]


def enrich_now(tid):
    async def go():
        pool = await db.create_pool()
        try:
            return await enrich.enrich_track(pool, tid)
        finally:
            await pool.close()
    return asyncio.run(go())


@pytest.mark.parametrize("dur,ok", [(262_000, True), (272_000, True), (272_001, False), (251_999, False)])
def test_is_match_duration_window(dur, ok):
    assert enrich.is_match(cand(dur=dur), "Tum Hi Ho", 262_000) is ok


def test_is_match_title_ratio_and_gaps():
    assert enrich.is_match(cand(title="Tum Hi Ho (From \"Aashiqui 2\")"), "Tum Hi Ho", 262_000)
    assert not enrich.is_match(cand(title="Completely Different Song"), "Tum Hi Ho", 262_000)
    assert enrich.is_match(cand(dur=None), "Tum Hi Ho", 262_000) and enrich.is_match(cand(), "Tum Hi Ho", None)  # unknown duration: title decides
    c = cand(); c["artists"] = []
    assert not enrich.is_match(c, "Tum Hi Ho", 262_000)


def test_enrichment_unifies_artists_and_adds_album_cover(client, ytm):
    state, root = ytm
    h = prof(client)
    a = ensure(client, h, "tum hi ho lyrical", "Arijit Singh Official")
    b = ensure(client, h, "kesariya video", "Arijit Singh")
    state["cands"] = [cand()]
    state["albums"]["MPREb_aashiqui"] = {"title": "Aashiqui 2", "year": "2013", "thumbnails": [{"url": "u1"}, {"url": "u2"}]}
    assert enrich_now(a["id"]) is True
    state["cands"] = [cand(title="Kesariya", album="Brahmastra", browse="MPREb_brahma")]
    state["albums"]["MPREb_brahma"] = {"title": "Brahmastra", "year": "2022", "thumbnails": [{"url": "u"}]}
    assert enrich_now(b["id"]) is True
    ta, tb = [client.get(f"/api/tracks/{t['id']}", headers=h).json() for t in (a, b)]
    assert ta["title"] == "Tum Hi Ho" and tb["title"] == "Kesariya"
    assert ta["artists"][0]["id"] == tb["artists"][0]["id"] and ta["artists"][0]["name"] == "Arijit Singh"  # one artist, not two
    assert row("SELECT count(*) AS n FROM artists WHERE name_norm LIKE 'arijit singh%'")["n"] == 1
    assert row("SELECT ytm_channel_id FROM artists WHERE id = $1", ta["artists"][0]["id"])["ytm_channel_id"] == "UCarijit"
    assert row("SELECT count(*) AS n FROM albums WHERE title_norm = 'aashiqui 2 official'")["n"] == 0  # the provisional album was dropped
    al = row("SELECT * FROM albums WHERE ytm_browse_id = 'MPREb_aashiqui'")
    assert al["title"] == "Aashiqui 2" and al["year"] == 2013 and al["cover_path"] == f"art/album/{al['id']}.jpg"
    dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(root / al["cover_path"])], capture_output=True, text=True).stdout.strip()
    assert dims == "600,600"  # square album art
    assert row("SELECT metadata_status, artwork_path FROM tracks WHERE id = $1", a["id"]) ["artwork_path"] == al["cover_path"]
    assert client.get(f"/api/art/track/{a['id']}").status_code == 200
    r = client.get("/api/search", params={"q": "arijit singh tum hi ho", "type": "songs"}, headers=h).json()  # search columns were refreshed
    assert r["cache_hit"] and r["songs"][0]["id"] == a["id"]
    assert state["queries"][0] == "Arijit Singh Official tum hi ho lyrical"


def test_no_match_manual_and_already_enriched_are_left_alone(client, ytm, monkeypatch):
    state, _ = ytm
    h = prof(client)
    t = ensure(client, h, "tum hi ho", "Someone")
    state["cands"] = [cand(dur=400_000)]  # far too long
    assert enrich_now(t["id"]) is False
    assert row("SELECT metadata_status, title FROM tracks WHERE id = $1", t["id"])["metadata_status"] == "provisional"
    state["cands"] = []
    assert enrich_now(t["id"]) is False
    run(lambda c: c.execute("UPDATE tracks SET metadata_status = 'manual' WHERE id = $1", t["id"]))
    state["cands"] = [cand()]
    assert enrich_now(t["id"]) is False and row("SELECT title FROM tracks WHERE id = $1", t["id"])["title"] == "tum hi ho"
    u = ensure(client, h, "tum hi ho", "Someone")
    state["albums"]["MPREb_aashiqui"] = {"title": "Aashiqui 2", "year": "2013", "thumbnails": []}
    assert enrich_now(u["id"]) is True and enrich_now(u["id"]) is False  # done once
    state["cands"] = [cand()]; state["queries"].clear()
    def boom(q): raise RuntimeError("ytmusicapi down")
    monkeypatch.setattr(youtube, "_ytm_search", boom)
    v = ensure(client, h, "tum hi ho", "Someone")
    assert enrich_now(v["id"]) is False  # a failing search is not an error


def test_provisional_twin_of_a_channel_artist_is_merged(client, ytm):
    state, _ = ytm
    h = prof(client); name = "Twin " + uuid.uuid4().hex[:6]; ch = "UC" + uuid.uuid4().hex[:8]
    t = ensure(client, h, "tum hi ho", name)
    async def mkchannel(c):
        return await c.fetchval("INSERT INTO artists (name, name_norm, ytm_channel_id) VALUES ($1, $2, $3) RETURNING id", name, name.lower(), ch)
    keep = run(mkchannel)  # same name, but with a channel id, existing next to the provisional row
    state["cands"] = [cand(artist=name, channel=ch, album=None)]
    assert enrich_now(t["id"]) is True
    assert row("SELECT primary_artist_id FROM tracks WHERE id = $1", t["id"])["primary_artist_id"] == keep
    assert row("SELECT count(*) AS n FROM artists WHERE name_norm = $1", name.lower())["n"] == 1


def test_merge_artists_handles_conflicts(client):
    h = prof(client)
    async def build(c):
        mk = lambda n, ch=None: c.fetchval("INSERT INTO artists (name, name_norm, ytm_channel_id) VALUES ($1, $1, $2) RETURNING id", f"{n}-{uuid.uuid4().hex[:6]}", ch)
        src, dst = await mk("src"), await mk("dst", "UC" + uuid.uuid4().hex[:8])
        alb_s = await c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ('Same', 'same', $1) RETURNING id", src)
        alb_d = await c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ('Same', 'same', $1) RETURNING id", dst)
        alb_o = await c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ('Other', 'other', $1) RETURNING id", src)
        t1 = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id, album_id) VALUES ('A', 'a', $1, $2) RETURNING id", src, alb_s)
        t2 = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id, album_id) VALUES ('B', 'b', $1, $2) RETURNING id", dst, alb_d)
        t3 = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id, album_id) VALUES ('C', 'c', $1, $2) RETURNING id", src, alb_o)
        for t, a in [(t1, src), (t2, dst), (t3, src), (t3, dst)]:  # t3 credits both: the merge must not create a duplicate credit
            await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, 'primary', 0)", t, a)
        return src, dst, (alb_s, alb_d, alb_o), (t1, t2, t3)
    src, dst, (alb_s, alb_d, alb_o), (t1, t2, t3) = run(build)
    async def go():
        pool = await db.create_pool()
        try:
            await enrich.merge_artists(pool, src, dst)
        finally:
            await pool.close()
    asyncio.run(go())
    assert row("SELECT count(*) AS n FROM artists WHERE id = $1", src)["n"] == 0
    assert row("SELECT count(*) AS n FROM tracks WHERE primary_artist_id = $1", dst)["n"] == 3
    assert row("SELECT count(*) AS n FROM track_artists WHERE track_id = $1", t3)["n"] == 1
    assert row("SELECT album_id FROM tracks WHERE id = $1", t1)["album_id"] == alb_d and row("SELECT count(*) AS n FROM albums WHERE id = $1", alb_s)["n"] == 0  # one "Same"
    assert row("SELECT artist_id FROM albums WHERE id = $1", alb_o)["artist_id"] == dst
    assert "dst" in row("SELECT artist_names_norm FROM tracks WHERE id = $1", t1)["artist_names_norm"]  # search columns refreshed


def test_worker_enriches_before_writing_tags_and_survives_failure(client, ytm, media, monkeypatch):  # noqa: F811
    state, root = ytm
    monkeypatch.setattr(worker, "POLITE_SLEEP", (0, 0))
    def fake_download(video_id, on_progress):
        a = storage.abs_path(storage.audio_rel(video_id, "m4a")); a.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(media / "a.m4a", a); shutil.copy(media / "t.jpg", a.with_suffix(".jpg"))
        return {"duration": 262, "title": "Tum Hi Ho (Official Video)", "channel": "T-Series"}
    monkeypatch.setattr(worker, "_download", fake_download)
    h = prof(client); t = ensure(client, h, "Tum Hi Ho (Official Video)", "T-Series")
    vid = t["video_id"]
    state["cands"] = [cand()]
    state["albums"]["MPREb_aashiqui"] = {"title": "Aashiqui 2", "year": "2013", "thumbnails": [{"url": "u"}]}
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, vid)))
    tags = MP4(root / storage.audio_rel(vid, "m4a"))
    al = row("SELECT cover_path FROM albums WHERE ytm_browse_id = 'MPREb_aashiqui'")["cover_path"]
    assert tags["\xa9nam"] == ["Tum Hi Ho"] and tags["\xa9ART"] == ["Arijit Singh"] and tags["\xa9alb"] == ["Aashiqui 2"] and tags["\xa9day"] == ["2013"]
    assert bytes(tags["covr"][0]) == (root / al).read_bytes()  # the embedded cover is the album art
    assert not (root / f"art/track/{vid}.jpg").exists()          # the video-frame crop was replaced
    # a broken enrichment must not fail the download
    def boom(q): raise RuntimeError("down")
    monkeypatch.setattr(youtube, "_ytm_search", boom)
    t2 = ensure(client, h, "Another", "Someone"); v2 = t2["video_id"]
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, v2)))
    assert row("SELECT status FROM download_jobs WHERE video_id = $1", v2)["status"] == "done"

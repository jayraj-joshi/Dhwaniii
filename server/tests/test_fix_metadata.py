"""M2.3-B: PATCH /api/tracks/{id}, GET .../candidates, POST .../sources."""
import asyncio
import shutil
import uuid

import pytest
from mutagen.mp4 import MP4

from app import config, storage, worker
from tests.conftest import FAKE_REMOTE
from tests.helpers import jobs, mk_track, prof, run
from tests.test_worker import media, with_ctx  # noqa: F401


def row(sql, *a):
    return run(lambda c: c.fetchrow(sql, *a))


def with_file(track, root, media_dir):
    """Give a track a real cached m4a on disk and a files row. Returns the file path."""
    rel = storage.audio_rel(track["video_id"], "m4a")
    p = root / rel; p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(media_dir / "a.m4a", p)
    run(lambda c: c.execute("INSERT INTO files (source_id, path, codec, bytes) SELECT id, $2, 'm4a', $3 FROM sources WHERE video_id = $1", track["video_id"], rel, p.stat().st_size))
    return p


def test_patch_sets_fields_manual_status_and_search(client):
    h = prof(client); t = mk_track(client, h, title="messy title (official)")
    word = "fx" + uuid.uuid4().hex[:6]
    r = client.patch(f"/api/tracks/{t['id']}", json={"title": f"Clean {word}", "artists": ["Main Artist", "Guest"], "album": "The Album", "year": 2019}, headers=h)
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == f"Clean {word}" and [a["name"] for a in d["artists"]] == ["Main Artist", "Guest"] and d["album"]["title"] == "The Album"
    assert row("SELECT metadata_status FROM tracks WHERE id = $1", t["id"])["metadata_status"] == "manual"
    assert row("SELECT year FROM albums WHERE id = $1", d["album"]["id"])["year"] == 2019
    assert row("SELECT count(*) AS n FROM artists WHERE id = $1", t["artists"][0]["id"])["n"] == 0  # the guessed artist row is gone
    found = client.get("/api/search", params={"q": f"{word} guest", "type": "songs"}, headers=h).json()["songs"]
    assert found and found[0]["id"] == t["id"]  # search columns were refreshed
    # partial edits leave the rest alone; null/"" clears the album
    d2 = client.patch(f"/api/tracks/{t['id']}", json={"title": f"Renamed {word}"}, headers=h).json()
    assert d2["album"]["title"] == "The Album" and [a["name"] for a in d2["artists"]] == ["Main Artist", "Guest"]
    assert client.patch(f"/api/tracks/{t['id']}", json={"album": ""}, headers=h).json()["album"] is None
    assert client.patch(f"/api/tracks/{t['id']}", json={"album": None}, headers=h).json()["album"] is None


def test_patch_validation_and_errors(client):
    h = prof(client); t = mk_track(client, h); url = f"/api/tracks/{t['id']}"
    assert client.patch(url, json={"year": 2001}, headers=h).status_code == 422          # a year needs an album
    assert client.patch(url, json={"title": ""}, headers=h).status_code == 422
    assert client.patch(url, json={"artists": []}, headers=h).status_code == 422
    assert client.patch(url, json={"year": 5}, headers=h).status_code == 422
    assert client.patch("/api/tracks/99999999", json={"title": "x"}, headers=h).status_code == 404
    assert client.patch(url, json={"title": "x"}).status_code == 401
    assert row("SELECT metadata_status FROM tracks WHERE id = $1", t["id"])["metadata_status"] == "provisional"  # failed edits changed nothing


def test_patch_rewrites_tags_of_a_cached_file_and_keeps_bytes_exact(client, tmp_path, monkeypatch, media):  # noqa: F811
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    h = prof(client); t = mk_track(client, h)
    path = with_file(t, tmp_path, media)
    client.patch(f"/api/tracks/{t['id']}", json={"title": "Fixed Title", "artists": ["Fixed Artist"], "album": "Fixed Album", "year": 2020}, headers=h)
    tags = MP4(path)
    assert tags["\xa9nam"] == ["Fixed Title"] and tags["\xa9ART"] == ["Fixed Artist"] and tags["\xa9alb"] == ["Fixed Album"] and tags["\xa9day"] == ["2020"]
    assert row("SELECT bytes FROM files WHERE path = $1", storage.audio_rel(t["video_id"], "m4a"))["bytes"] == path.stat().st_size


def test_enrichment_never_overrides_a_manual_edit(client, monkeypatch):
    from app import youtube
    from tests.test_enrich import cand, enrich_now
    h = prof(client); t = mk_track(client, h, title="tum hi ho")
    client.patch(f"/api/tracks/{t['id']}", json={"title": "My Own Title"}, headers=h)
    monkeypatch.setattr(youtube, "_ytm_search", lambda q: [cand()])
    assert enrich_now(t["id"]) is False and row("SELECT title FROM tracks WHERE id = $1", t["id"])["title"] == "My Own Title"


def test_candidates_are_scored_against_this_tracks_length(client):
    h = prof(client); t = mk_track(client, h, title="Tum Hi Ho", duration_ms=262_000)
    other = mk_track(client, h)
    mk = lambda vid, dur, **kw: {"video_id": vid, "title": "Tum Hi Ho", "artists": [{"name": "X", "ytm_channel_id": None}], "album": None, "duration_ms": dur,
                                 "thumb_url": None, "channel": "Chan", "quality_score": 0, "from_ytm": True, "view_count": 1000, **kw}
    FAKE_REMOTE[:] = [mk("wrongLength1", 200_000), mk("rightLength1", 263_000), mk(other["video_id"], 262_000)]
    try:
        r = client.get(f"/api/tracks/{t['id']}/candidates", headers=h).json()
    finally:
        FAKE_REMOTE.clear()
    assert {c["video_id"] for c in r[:2]} == {other["video_id"], "rightLength1"} and r[-1]["video_id"] == "wrongLength1"  # equal scores keep their order
    by = {c["video_id"]: c for c in r}
    assert by[other["video_id"]]["in_library"] and by[other["video_id"]]["track_id"] == other["id"] and not by["rightLength1"]["in_library"]
    assert client.get("/api/tracks/99999999/candidates", headers=h).status_code == 404


def test_switching_source_keeps_the_track_and_replaces_the_file(client, tmp_path, monkeypatch, media):  # noqa: F811
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path)); monkeypatch.setattr(worker, "POLITE_SLEEP", (0, 0))
    h = prof(client); t = mk_track(client, h)
    old_vid, new_vid = t["video_id"], "sw" + uuid.uuid4().hex[:9]
    old_path = with_file(t, tmp_path, media)
    pl = client.post("/api/playlists", json={"name": "keep me"}, headers=h).json()
    client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": [t["id"]]}, headers=h)
    r = client.post(f"/api/tracks/{t['id']}/sources", json={"video_id": new_vid, "channel": "Better Chan", "duration_ms": 200_000}, headers=h)
    assert r.status_code == 201 and r.json()["id"] == t["id"]
    assert r.json()["video_id"] == old_vid and r.json()["cache_state"] == "cached"          # still plays the old file until the new one arrives
    assert jobs([new_vid]) == {new_vid: ("queued", "high")}
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", f"dl:{new_vid}") is not None
    assert client.post(f"/api/tracks/{t['id']}/sources", json={"video_id": new_vid}, headers=h).status_code == 201  # idempotent
    assert len(client.get(f"/api/tracks/{t['id']}", headers=h).json()["sources"]) == 2

    def fake_download(video_id, on_progress):
        a = storage.abs_path(storage.audio_rel(video_id, "m4a")); a.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(media / "a.m4a", a)
        return {"duration": 200, "title": "whatever", "channel": "Better Chan"}
    monkeypatch.setattr(worker, "_download", fake_download)
    asyncio.run(with_ctx(lambda ctx: worker.download_track(ctx, new_vid)))
    d = client.get(f"/api/tracks/{t['id']}", headers=h).json()
    assert d["id"] == t["id"] and d["video_id"] == new_vid and d["cache_state"] == "cached"
    assert not old_path.exists() and row("SELECT count(*) AS n FROM files f JOIN sources s ON s.id = f.source_id WHERE s.track_id = $1", t["id"])["n"] == 1  # old file gone
    assert [i["track"]["id"] for i in client.get(f"/api/playlists/{pl['id']}", headers=h).json()["items"]] == [t["id"]]  # the playlist still works


def test_add_source_errors(client):
    h = prof(client); a, b = mk_track(client, h), mk_track(client, h)
    assert client.post(f"/api/tracks/{a['id']}/sources", json={"video_id": b["video_id"]}, headers=h).status_code == 409  # belongs to another track
    assert client.post(f"/api/tracks/{a['id']}/sources", json={"video_id": "bad id!"}, headers=h).status_code == 422
    assert client.post("/api/tracks/99999999/sources", json={"video_id": "abcdefghijk"}, headers=h).status_code == 404
    assert client.post(f"/api/tracks/{a['id']}/sources", json={"video_id": "abcdefghijk"}).status_code == 401

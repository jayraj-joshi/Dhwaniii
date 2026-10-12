import asyncio
import uuid
from pathlib import Path

import asyncpg
import pytest

from app import config
from tests.conftest import TEST_DB, _url


def profile(client):
    return {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"t-{uuid.uuid4().hex[:8]}"}).json()["id"])}


def hint(vid, **kw):
    return {"video_id": vid, "title": "Tum Hi Ho", "artists": [{"name": "Arijit Singh", "ytm_channel_id": "UCarijit1"}],
            "album": {"title": "Aashiqui 2", "ytm_browse_id": "MPREb_1"}, "duration_ms": 262000, "channel": "Arijit Singh - Topic",
            "quality_score": 4.5, **kw}


def counts():
    async def go():
        c = await asyncpg.connect(_url(TEST_DB))
        r = [await c.fetchval(f"SELECT count(*) FROM {t}") for t in ("artists", "albums", "tracks", "sources")]
        await c.close()
        return r
    return asyncio.run(go())


def test_ensure_twice_same_id_no_extra_rows(client):
    h = profile(client)
    a = client.post("/api/tracks/ensure", json=[hint("ensure00001")], headers=h).json()[0]
    before = counts()
    b = client.post("/api/tracks/ensure", json=[hint("ensure00001")], headers=h).json()[0]
    assert a["id"] == b["id"] and counts() == before
    assert a["video_id"] == "ensure00001" and a["cache_state"] == "remote" and a["liked"] is False
    assert a["artists"][0]["name"] == "Arijit Singh" and a["album"]["title"] == "Aashiqui 2" and a["gain_db"] is None


def test_artist_shared_across_tracks_and_order_kept(client):
    h = profile(client)
    out = client.post("/api/tracks/ensure", json=[hint("ensure00002", title="Hawayein"), hint("ensure00003", title="Zaalima"), hint("ensure00002")], headers=h).json()
    assert [t["title"] for t in out] == ["Hawayein", "Zaalima", "Hawayein"] and out[0]["id"] == out[2]["id"]
    assert out[0]["artists"][0]["id"] == out[1]["artists"][0]["id"]


def test_no_artist_hint_falls_back_to_title_and_channel(client):
    h = profile(client)
    t = client.post("/api/tracks/ensure", json=[{"video_id": "ensure00004", "title": "Coldplay - Yellow (Official Video)"}], headers=h).json()[0]
    assert t["title"] == "Yellow" and t["artists"][0]["name"] == "Coldplay"
    t = client.post("/api/tracks/ensure", json=[{"video_id": "ensure00005", "title": "Blank Space", "channel": "Taylor SwiftVEVO"}], headers=h).json()[0]
    assert t["artists"][0]["name"] == "Taylor Swift"


def test_concurrent_ensure_creates_one_track(client):
    import concurrent.futures as cf
    h = profile(client)
    with cf.ThreadPoolExecutor(8) as ex:
        ids = list(ex.map(lambda _: client.post("/api/tracks/ensure", json=[hint("ensure00006")], headers=h).json()[0]["id"], range(8)))
    assert len(set(ids)) == 1


def test_invalid_video_ids_rejected(client):
    h = profile(client)
    for bad in ["short", "../../etc/pa", "has space!!", "waytoolongvideoid", "bad;rm -rf/"]:
        assert client.post("/api/tracks/ensure", json=[hint(bad)], headers=h).status_code == 422
    assert client.post("/api/tracks/ensure", json=[], headers=h).json() == []
    assert client.post("/api/tracks/ensure", json=[hint(f"ensur{i:06d}") for i in range(201)], headers=h).status_code == 422
    assert client.post("/api/tracks/ensure", json=[hint("ensure00007")]).status_code == 401


def test_get_track_and_art(client, tmp_path, monkeypatch):
    h = profile(client)
    t = client.post("/api/tracks/ensure", json=[hint("ensure00008")], headers=h).json()[0]
    d = client.get(f"/api/tracks/{t['id']}", headers=h).json()
    assert d["id"] == t["id"] and d["sources"][0]["video_id"] == "ensure00008" and d["sources"][0]["cached"] is False
    assert client.get("/api/tracks/99999999", headers=h).status_code == 404
    # art: 404 with no file, no profile needed, 200 once a file exists, traversal refused
    assert client.get(f"/api/art/track/{t['id']}").status_code == 404
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    (tmp_path / "art/track").mkdir(parents=True)
    (tmp_path / "art/track/x.jpg").write_bytes(b"\xff\xd8jpeg")

    async def setp(rel):
        c = await asyncpg.connect(_url(TEST_DB)); await c.execute("UPDATE tracks SET artwork_path = $2 WHERE id = $1", t["id"], rel); await c.close()
    asyncio.run(setp("art/track/x.jpg"))
    r = client.get(f"/api/art/track/{t['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "max-age" in r.headers["cache-control"]
    asyncio.run(setp("../../etc/passwd"))
    assert client.get(f"/api/art/track/{t['id']}").status_code == 404
    assert client.get("/api/art/nonsense/1").status_code == 404

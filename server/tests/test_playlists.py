import subprocess

import pytest

from app import config
from tests.helpers import jobs, mk_track, prof, run


def mkpl(client, h, name="List"):
    r = client.post("/api/playlists", json={"name": name}, headers=h)
    assert r.status_code == 201
    return r.json()


def add(client, h, pid, ids, at="end"):
    r = client.post(f"/api/playlists/{pid}/tracks", json={"track_ids": ids, "at": at}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def order(client, h, pid):
    return [(i["item_id"], i["track"]["id"]) for i in client.get(f"/api/playlists/{pid}", headers=h).json()["items"]]


def test_crud_and_summary_fields(client):
    h = prof(client); pl = mkpl(client, h, "  Road trip ")
    assert pl["name"] == "Road trip" and pl["track_count"] == 0 and pl["collage"] == [] and pl["cover_url"] is None and pl["pinned_to_sidebar"] is False
    assert client.get("/api/playlists", headers=h).json()[0]["id"] == pl["id"]
    up = client.patch(f"/api/playlists/{pl['id']}", json={"name": "Trip", "description": "d", "pinned_to_sidebar": True}, headers=h).json()
    assert (up["name"], up["description"], up["pinned_to_sidebar"]) == ("Trip", "d", True)
    assert client.patch(f"/api/playlists/{pl['id']}", json={"description": "only this"}, headers=h).json()["name"] == "Trip"
    assert client.post("/api/playlists", json={"name": ""}, headers=h).status_code == 422
    # findable by the new name in search (playlists tab)
    assert [p["id"] for p in client.get("/api/search", params={"q": "trip", "type": "playlists"}, headers=h).json()["playlists"]] == [pl["id"]]
    assert client.delete(f"/api/playlists/{pl['id']}", headers=h).status_code == 204
    assert client.get(f"/api/playlists/{pl['id']}", headers=h).status_code == 404


def test_playlists_belong_to_their_owner(client):
    h1, h2 = prof(client), prof(client); pl = mkpl(client, h1); t = mk_track(client, h1)
    item = add(client, h1, pl["id"], [t["id"]])[0]
    for method, path, kw in [("get", f"/api/playlists/{pl['id']}", {}), ("patch", f"/api/playlists/{pl['id']}", {"json": {"name": "x"}}),
                             ("delete", f"/api/playlists/{pl['id']}", {}), ("post", f"/api/playlists/{pl['id']}/tracks", {"json": {"track_ids": [t["id"]]}}),
                             ("patch", f"/api/playlists/{pl['id']}/tracks", {"json": {"item_id": item["item_id"]}}),
                             ("delete", f"/api/playlists/{pl['id']}/tracks", {"json": {"item_ids": [item["item_id"]]}}),
                             ("delete", f"/api/playlists/{pl['id']}/cover", {})]:
        assert client.request(method, path, headers=h2, **kw).status_code == 404, (method, path)
    assert client.get("/api/playlists", headers=h2).json() == []
    assert client.get(f"/api/playlists/{pl['id']}").status_code == 401
    assert order(client, h1, pl["id"]) == [(item["item_id"], t["id"])]  # untouched


def test_add_order_duplicates_and_positions(client):
    h = prof(client); pl = mkpl(client, h); a, b, c, x, y = (mk_track(client, h) for _ in range(5))
    add(client, h, pl["id"], [a["id"], b["id"], c["id"]])
    add(client, h, pl["id"], [x["id"], y["id"]], at="start")
    add(client, h, pl["id"], [a["id"]])  # the same track again is a separate row
    assert [t for _, t in order(client, h, pl["id"])] == [x["id"], y["id"], a["id"], b["id"], c["id"], a["id"]]
    d = client.get(f"/api/playlists/{pl['id']}", headers=h).json()
    assert d["track_count"] == 6 and len(d["collage"]) == 4 and d["items"][0]["track"]["id"] == x["id"] and d["items"][0]["position"] < d["items"][1]["position"]
    assert client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": [a["id"], 99999999]}, headers=h).status_code == 422
    assert client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": []}, headers=h).status_code == 422
    assert client.get(f"/api/playlists/{pl['id']}", headers=h).json()["track_count"] == 6  # the rejected batch added nothing
    for i in range(60):  # many inserts at the start never need renumbering (D18)
        add(client, h, pl["id"], [b["id"]], at="start")
    got = order(client, h, pl["id"])
    assert len(got) == 66 and got[-6:][0][1] == x["id"]


def test_move_remove(client):
    h = prof(client); pl = mkpl(client, h); ts = [mk_track(client, h) for _ in range(4)]
    items = add(client, h, pl["id"], [t["id"] for t in ts]); ids = [i["item_id"] for i in items]
    mv = lambda item, before: client.patch(f"/api/playlists/{pl['id']}/tracks", json={"item_id": item, "before_item_id": before}, headers=h)
    assert mv(ids[3], ids[0]).status_code == 200                      # last to the very front
    assert [i for i, _ in order(client, h, pl["id"])] == [ids[3], ids[0], ids[1], ids[2]]
    mv(ids[3], ids[2])                                                 # between item 1 and item 2
    assert [i for i, _ in order(client, h, pl["id"])] == [ids[0], ids[1], ids[3], ids[2]]
    mv(ids[0], None)                                                   # to the end
    assert [i for i, _ in order(client, h, pl["id"])] == [ids[1], ids[3], ids[2], ids[0]]
    assert mv(ids[1], ids[1]).status_code == 200 and [i for i, _ in order(client, h, pl["id"])][0] == ids[1]  # no-op
    for _ in range(40):                                                # squeeze repeatedly into the same gap: exact numeric halves
        mv(ids[0], ids[3]); mv(ids[2], ids[3])
    assert len(order(client, h, pl["id"])) == 4
    assert mv(999999999, None).status_code == 404 and mv(ids[1], 999999999).status_code == 422
    r = client.request("DELETE", f"/api/playlists/{pl['id']}/tracks", json={"item_ids": [ids[1], 424242]}, headers=h)
    assert r.status_code == 204 and len(order(client, h, pl["id"])) == 3
    # removing one of two rows of the same track removes just that row
    dup = add(client, h, pl["id"], [ts[0]["id"]])[0]["item_id"]
    client.request("DELETE", f"/api/playlists/{pl['id']}/tracks", json={"item_ids": [dup]}, headers=h)
    assert sum(1 for _, t in order(client, h, pl["id"]) if t == ts[0]["id"]) == 1


def test_adding_requests_low_downloads_and_opening_warms_first_five(client):
    h = prof(client); pl = mkpl(client, h); ts = [mk_track(client, h) for _ in range(8)]
    add(client, h, pl["id"], [t["id"] for t in ts[:2]])
    assert set(jobs([t["video_id"] for t in ts[:2]]).values()) == {("queued", "low")}
    # build a playlist behind the API's back, so only opening it can request downloads
    pl2 = mkpl(client, h, "Warm")
    run(lambda c: c.executemany("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES ($1, $2, $3)", [(pl2["id"], t["id"], i) for i, t in enumerate(ts)]))
    assert jobs([t["video_id"] for t in ts[2:]]) == {}
    client.get(f"/api/playlists/{pl2['id']}", headers=h)
    got = jobs([t["video_id"] for t in ts])
    assert {v for v in got} == {t["video_id"] for t in ts[:5]}  # exactly the first five; the 2 from before are among them
    assert all(v == ("queued", "low") for v in got.values())


@pytest.fixture
def png(tmp_path_factory):
    d = tmp_path_factory.mktemp("img")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=800x400", "-frames:v", "1", str(d / "c.png")], check=True)
    return (d / "c.png").read_bytes()


def test_cover_upload_validation_and_removal(client, png, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    h = prof(client); pl = mkpl(client, h)
    put = lambda data, name="c.png": client.put(f"/api/playlists/{pl['id']}/cover", files={"file": (name, data, "application/octet-stream")}, headers=h)
    r = put(png)
    assert r.status_code == 200 and r.json()["cover_url"].startswith(f"/api/art/playlist/{pl['id']}?v=")
    art = client.get(f"/api/art/playlist/{pl['id']}")
    assert art.status_code == 200 and art.headers["content-type"] == "image/jpeg"
    dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(tmp_path / f"art/playlist/{pl['id']}.jpg")], capture_output=True, text=True).stdout.strip()
    assert dims == "600,600"
    assert put(b"not an image at all").status_code == 422 and put(b"\x89PNG\r\n\x1a\n" + b"garbage").status_code == 422
    assert put(b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)).status_code == 413
    assert client.get(f"/api/playlists/{pl['id']}", headers=h).json()["cover_url"] is not None  # failed uploads changed nothing
    assert client.delete(f"/api/playlists/{pl['id']}/cover", headers=h).status_code == 204
    assert client.get(f"/api/art/playlist/{pl['id']}").status_code == 404
    put(png); assert client.delete(f"/api/playlists/{pl['id']}", headers=h).status_code == 204
    assert not (tmp_path / f"art/playlist/{pl['id']}.jpg").exists()  # deleting a playlist removes its cover file

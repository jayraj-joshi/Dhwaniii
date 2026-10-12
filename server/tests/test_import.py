"""M3.4-B: CSV and YouTube playlist import."""
import asyncio
import json

import pytest

from app import importer, youtube
from tests.helpers import jobs, prof, run
from tests.test_worker import with_ctx

EXPORTIFY = ("Track URI,Track Name,Album Name,Artist Name(s),Release Date,Duration (ms),Popularity\n"
             'spotify:track:1,Tum Hi Ho,Aashiqui 2,Arijit Singh,2013,262000,80\n'
             'spotify:track:2,Lover,MoonChild Era,"Diljit Dosanjh;Raj",2017,190000,70\n'
             'spotify:track:3,Not Findable,Nowhere,Nobody,2020,200000,1\n')


def test_parse_csv_variants():
    rows = importer.parse_csv(EXPORTIFY.encode())
    assert rows[0] == {"title": "Tum Hi Ho", "artist": "Arijit Singh", "album": "Aashiqui 2", "duration_ms": 262000}
    assert rows[1]["artist"] == "Diljit Dosanjh" and len(rows) == 3                       # several artists: the first one
    g = importer.parse_csv("﻿title,artist,album,duration_ms\nKesariya,Arijit Singh,,\nHusn,Anuv Jain,Husn,191000\n".encode())
    assert g[0] == {"title": "Kesariya", "artist": "Arijit Singh", "album": None, "duration_ms": None} and g[1]["duration_ms"] == 191000
    assert importer.parse_csv(b"Track Name\nOnly a title\n")[0]["artist"] == ""
    for bad, msg in [(b"", "header"), (b"artist,album\nx,y\n", "title column"), (b"title\n", "no rows"), (b"\xff\xfe\x00bad", "UTF-8")]:
        with pytest.raises(ValueError, match=msg):
            importer.parse_csv(bad)


@pytest.mark.parametrize("url,expected", [
    ("https://music.youtube.com/playlist?list=PLabcdefghijk123", "PLabcdefghijk123"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijk123&index=2", "PLabcdefghijk123"),
    ("http://youtube.com/playlist?list=OLAK5uy_abcdefghijk", "OLAK5uy_abcdefghijk"),
    ("https://evil.example/playlist?list=PLabcdefghijk123", None), ("https://youtube.com.evil.example/playlist?list=PLabcdefghijk123", None),
    ("file:///etc/passwd", None), ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None), ("https://youtube.com/playlist?list=short", None),
    ("https://youtube.com/playlist?list=PL abc;rm -rf /", None), ("not a url", None),
])
def test_playlist_id_from_url(url, expected):
    assert importer.playlist_id_from_url(url) == expected


def cand(vid, title, artist, dur, ch="UCx"):
    return {"video_id": vid, "title": title, "artists": [{"name": artist, "ytm_channel_id": ch}], "from_ytm": True, "channel": None,
            "view_count": None, "album": None, "duration_ms": dur, "thumb_url": None}


@pytest.fixture
def imp(client, monkeypatch):
    monkeypatch.setattr(importer, "SEARCH_PAUSE", 0)


def start(client, h, **kw):
    r = client.post("/api/import", headers=h, **kw)
    assert r.status_code == 202, r.text
    return r.json()


def csv_upload(text, name="My Songs.csv", **extra):
    return {"files": {"file": (name, text, "text/csv")}, "data": extra}


def run_job(import_id):
    asyncio.run(with_ctx(lambda ctx: importer.run_import(ctx, import_id)))


def test_csv_import_matches_in_order_and_reports_the_rest(client, imp, monkeypatch):
    h = prof(client)
    tag = "i" + __import__("uuid").uuid4().hex[:3]
    def search(q):
        if "Tum Hi Ho" in q:
            return [cand(f"{tag}wrongdu", "Tum Hi Ho", "Arijit Singh", 300_000), cand(f"{tag}tumhiho", "Tum Hi Ho", "Arijit Singh", 262_500)]
        if "Lover" in q:
            return [cand(f"{tag}lover00", "Lover", "Diljit Dosanjh", 190_000)]
        if "Findable" in q:
            return []
        raise RuntimeError("ytmusicapi down")
    monkeypatch.setattr(youtube, "_ytm_search", search)
    csv_text = EXPORTIFY + "x,Explodes,A,Someone,2020,1000,1\nx,,A,Someone,2020,1000,1\n"
    r = start(client, h, **csv_upload(csv_text))
    pl = client.get(f"/api/playlists/{r['playlist_id']}", headers=h).json()
    assert pl["name"] == "My Songs" and pl["items"] == []                       # the playlist exists at once, named after the file
    st = client.get(f"/api/imports/{r['import_id']}", headers=h).json()
    assert st["status"] == "running" and st["total"] == 5 and st["matched"] == 0
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", f"import:{r['import_id']}") is not None
    run_job(r["import_id"])
    st = client.get(f"/api/imports/{r['import_id']}", headers=h).json()
    assert (st["status"], st["total"], st["matched"], st["playlist_id"], st["error"]) == ("done", 5, 2, r["playlist_id"], None)
    assert [(u["row"], u["title"], u["reason"][:13]) for u in st["unmatched"]] == [(3, "Not Findable", "no match"), (4, "Explodes", "search failed"), (5, "", "no title")]
    items = client.get(f"/api/playlists/{r['playlist_id']}", headers=h).json()["items"]
    assert [i["track"]["video_id"] for i in items] == [f"{tag}tumhiho", f"{tag}lover00"]       # CSV order; the wrong-length upload was refused
    assert set(jobs([f"{tag}tumhiho", f"{tag}lover00"]).values()) == {("queued", "low")}        # matched tracks are fetched at low priority
    assert run(lambda c: c.fetchval("SELECT payload FROM imports WHERE id = $1", r["import_id"])) is None  # the parsed rows are dropped when done


def test_progress_events_reach_the_profile(client, imp, monkeypatch):
    h = prof(client); pid = int(h["X-Profile-Id"]); dev = "imp" + __import__("uuid").uuid4().hex[:6]
    monkeypatch.setattr(youtube, "_ytm_search", lambda q: [])
    r = start(client, h, **csv_upload("title,artist\nA,B\nC,D\n"))
    with client.websocket_connect(f"/ws?profile={pid}&device={dev}") as ws:
        run_job(r["import_id"])
        seen = []
        for _ in range(30):
            m = ws.receive_json()
            if m["type"] == "import_progress":
                seen.append((m["import_id"], m["done"], m["total"]))
                if m["done"] == m["total"] == 2 and len(seen) >= 3:
                    break
    assert seen[0][1:] == (0, 2) and (r["import_id"], 2, 2) in seen and [d for _, d, _ in seen] == sorted(d for _, d, _ in seen)


def test_youtube_import(client, imp, monkeypatch):
    h = prof(client); tag = "y" + __import__("uuid").uuid4().hex[:4]
    entries = [{"id": f"{tag}aaaaa1", "title": "Arijit Singh - Tum Hi Ho (Official Video)", "channel": "T-Series", "duration": 262},
               {"id": f"{tag}aaaaa2", "title": "[Private video]", "channel": None},
               {"id": f"{tag}aaaaa3", "title": "Kesariya", "uploader": "Sony Music India", "duration": 269.5}]
    seen_ids = []
    monkeypatch.setattr(importer, "_yt_playlist", lambda lid: (seen_ids.append(lid), {"title": "Road Trip Mix", "entries": entries})[1])
    r = start(client, h, json={"url": "https://music.youtube.com/watch?v=abc&list=PLroadtrip12345"})
    assert client.get(f"/api/playlists/{r['playlist_id']}", headers=h).json()["name"] == "Imported playlist"
    run_job(r["import_id"])
    assert seen_ids == ["PLroadtrip12345"]          # only the id reached yt-dlp, not the URL
    st = client.get(f"/api/imports/{r['import_id']}", headers=h).json()
    assert (st["status"], st["total"], st["matched"]) == ("done", 3, 2) and st["unmatched"][0]["reason"] == "unavailable" and st["unmatched"][0]["row"] == 2
    pl = client.get(f"/api/playlists/{r['playlist_id']}", headers=h).json()
    assert pl["name"] == "Road Trip Mix" and [i["track"]["video_id"] for i in pl["items"]] == [f"{tag}aaaaa1", f"{tag}aaaaa3"]
    assert pl["items"][0]["track"]["title"] == "Tum Hi Ho" and pl["items"][0]["track"]["artists"][0]["name"] == "Arijit Singh"
    # a name given by the caller is kept
    r2 = start(client, h, json={"url": "https://youtube.com/playlist?list=PLroadtrip12345", "name": "Mine"})
    run_job(r2["import_id"])
    assert client.get(f"/api/playlists/{r2['playlist_id']}", headers=h).json()["name"] == "Mine"


def test_failed_import_is_recorded(client, imp, monkeypatch):
    h = prof(client)
    def boom(lid): raise RuntimeError("yt-dlp blew up")
    monkeypatch.setattr(importer, "_yt_playlist", boom)
    r = start(client, h, json={"url": "https://youtube.com/playlist?list=PLbroken123456"})
    run_job(r["import_id"])
    st = client.get(f"/api/imports/{r['import_id']}", headers=h).json()
    assert st["status"] == "failed" and "blew up" in st["error"]
    run_job(r["import_id"])  # running it again does nothing: only a `running` import is worked on
    assert client.get(f"/api/imports/{r['import_id']}", headers=h).json()["status"] == "failed"


def test_validation_and_ownership(client, imp):
    h, other = prof(client), prof(client)
    assert client.post("/api/import", json={"url": "https://example.com/x"}, headers=h).status_code == 422
    assert client.post("/api/import", json={"url": "https://youtube.com/watch?v=abc"}, headers=h).status_code == 422
    assert client.post("/api/import", json={"nope": 1}, headers=h).status_code == 422
    assert client.post("/api/import", content=b"not json", headers={**h, "content-type": "application/json"}).status_code == 422
    assert client.post("/api/import", headers=h, **csv_upload("artist,album\nx,y\n")).status_code == 422
    assert client.post("/api/import", headers=h, **csv_upload("title\n" + "x\n" * 10)["files"] and {"files": {"file": ("a.csv", "title\n" + "a" * (2 * 1024 * 1024 + 1), "text/csv")}}).status_code == 413
    assert client.post("/api/import", headers=h, data={"name": "no file"}, files={"other": ("a.csv", "title\nx\n")}).status_code == 422
    assert client.post("/api/import", json={"url": "https://youtube.com/playlist?list=PLabcdefghijk123"}).status_code == 401
    assert run(lambda c: c.fetchval("SELECT count(*) FROM imports WHERE profile_id = $1", int(h["X-Profile-Id"]))) == 0  # rejected requests created nothing
    r = start(client, h, json={"url": "https://youtube.com/playlist?list=PLabcdefghijk123"})
    assert client.get(f"/api/imports/{r['import_id']}", headers=other).status_code == 404 and client.get("/api/imports/99999999", headers=h).status_code == 404

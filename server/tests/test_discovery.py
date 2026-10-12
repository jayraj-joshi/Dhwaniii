"""M3.1-B: artist/album pages, follows, saved albums, and the YouTube tabs in search. YouTube Music is faked with real-shaped payloads."""
import subprocess
import uuid

import pytest

from app import enrich, storage
from app.routes import discovery
from tests.helpers import mk_track, prof, run


@pytest.fixture
def ytm(client, tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=900x300", "-frames:v", "1", str(tmp_path / "src.png")], check=True)
    monkeypatch.setattr(enrich, "_fetch_cover", lambda url, dst: storage.crop_art(tmp_path / "src.png", dst))
    st = {"artist": {}, "album": {}, "search": {}, "calls": []}
    def count(kind, table):
        def fn(key, *rest):
            st["calls"].append((kind, key))
            return table[key]
        return fn
    monkeypatch.setattr(discovery, "_yt_artist", count("artist", st["artist"]))
    monkeypatch.setattr(discovery, "_yt_album", count("album", st["album"]))
    monkeypatch.setattr(discovery, "_yt_search_kind", lambda q, kind: (st["calls"].append((kind, q)), st["search"][(kind, q)])[1])
    st["root"] = tmp_path
    return st


def uid():
    return uuid.uuid4().hex[:10]


def thumbs():
    return [{"url": "https://img/small", "width": 60, "height": 60}, {"url": "https://img/large", "width": 900, "height": 300}]


def song(vid, title, ch, name="Test Artist", album=("Album One", "MPREb_one")):
    return {"videoId": vid, "title": title, "artists": [{"name": name, "id": ch}], "album": {"name": album[0], "id": album[1]}, "thumbnails": thumbs()}


def artist_payload(ch, name="Test Artist", songs=(), albums=(), singles=(), related=()):
    return {"name": name, "channelId": ch, "description": f"{name} is a singer.", "thumbnails": thumbs(),
            "songs": {"browseId": "x", "results": list(songs)}, "albums": {"browseId": "x", "results": [{"title": t, "browseId": b, "year": y, "thumbnails": thumbs()} for t, b, y in albums]},
            "singles": {"browseId": "x", "results": [{"title": t, "browseId": b, "year": y, "thumbnails": thumbs()} for t, b, y in singles]},
            "related": {"browseId": "x", "results": [{"title": n, "browseId": c, "subscribers": "1M", "thumbnails": thumbs()} for n, c in related]}}


def album_payload(title, artist, ch, year="2024", tracks=()):
    return {"title": title, "year": year, "artists": [{"name": artist, "id": ch}], "thumbnails": thumbs(),
            "tracks": [{"videoId": v, "title": t, "artists": [{"name": artist, "id": ch}], "album": title, "duration_seconds": 200, "likeStatus": "INDIFFERENT"} for v, t in tracks]}


def vid():
    return "d" + uuid.uuid4().hex[:10]


def test_artist_by_ytm_id_creates_one_row_and_builds_the_page(client, ytm):
    h = prof(client); ch = "UC" + uid() + "abcdefgh"; v1, v2 = vid(), vid(); rel = "UC" + uid() + "relatedxx"
    ytm["artist"][ch] = artist_payload(ch, songs=[song(v1, "Top Song", ch), song(v2, "Second Song", ch)], albums=[("Album One", "MPREb_one", "2020"), ("Album Two", "MPREb_two", "2022")],
                                       singles=[("A Single", "MPREb_single", None)], related=[("Related Artist", rel)])
    p = client.get(f"/api/artists/ytm:{ch}", headers=h).json()
    assert isinstance(p["id"], int) and p["name"] == "Test Artist" and p["ytm_channel_id"] == ch and p["bio"] == "Test Artist is a singer."
    assert client.get(f"/api/artists/{p['id']}", headers=h).json()["id"] == p["id"]  # the numeric URL works from now on
    assert run(lambda c: c.fetchval("SELECT count(*) FROM artists WHERE ytm_channel_id = $1", ch)) == 1
    assert [(d["title"], d["type"], d["year"], d["id"]) for d in p["discography"]] == [("Album One", "album", 2020, None), ("Album Two", "album", 2022, None), ("A Single", "single", None, None)]
    assert p["discography"][0]["ytm_browse_id"] == "MPREb_one" and p["discography"][0]["thumb_url"] == "https://img/large"
    assert p["fans_also_like"] == [{"id": None, "name": "Related Artist", "ytm_channel_id": rel, "thumb_url": "https://img/large"}]
    assert [x["video_id"] for x in p["popular"]] == [v1, v2] and not p["popular"][0]["in_library"] and not p["popular"][1]["in_library"] and p["popular"][0]["plays"] == 0
    assert p["popular"][0]["artists"][0]["ytm_channel_id"] == ch and p["popular"][0]["album"]["ytm_browse_id"] == "MPREb_one"
    assert client.get(f"/api/art/artist/{p['id']}").status_code == 200  # the hero image was fetched and squared
    assert sum(1 for k, _ in ytm["calls"] if k == "artist") == 1  # one YouTube Music call across all of the above (Redis cache)
    assert p["followed"] is False and p["plays_30d"] == 0


def test_popular_follows_youtube_order_then_fills_with_your_plays(client, ytm):
    h = prof(client); ch = "UC" + uid() + "abcdefgh"; top = vid(); mine = vid()
    ytm["artist"][ch] = artist_payload(ch, songs=[song(top, "Global Hit", ch), song(mine, "Quiet Favourite", ch)])
    first = client.get(f"/api/artists/ytm:{ch}", headers=h).json()
    t = client.post("/api/tracks/ensure", json=[{"video_id": mine, "title": "Quiet Favourite", "artists": [{"name": "Test Artist", "ytm_channel_id": ch}], "duration_ms": 100_000}], headers=h).json()[0]
    assert t["artists"][0]["id"] == first["id"]
    plays = [{"track_id": t["id"], "played_at": "2099-01-01T00:00:00Z", "ms_played": 60_000, "context": "artist:%d" % first["id"]}] * 3
    client.post("/api/me/history", json=plays, headers=h)
    p = client.get(f"/api/artists/{first['id']}", headers=h).json()
    # YouTube Music's own Top Songs order wins (its best proxy for most-viewed), even though "mine" has more of this listener's own plays
    assert [(x["video_id"], x["plays"], x["in_library"]) for x in p["popular"]] == [(top, 0, False), (mine, 3, True)]
    assert p["popular"][1]["track_id"] == t["id"] and p["plays_30d"] == 3


def test_local_artist_without_youtube_music(client, ytm):
    h = prof(client); t = mk_track(client, h, title="Local Song")
    aid = t["artists"][0]["id"]
    run(lambda c: c.execute("INSERT INTO albums (title, title_norm, artist_id, year) VALUES ('Local Album', 'local album', $1, 2001)", aid))
    p = client.get(f"/api/artists/{aid}", headers=h).json()
    assert p["ytm_channel_id"] is None and [x["track_id"] for x in p["popular"]] == [t["id"]] and p["fans_also_like"] == []
    assert [(d["title"], d["year"]) for d in p["discography"]] == [("Local Album", 2001)]
    assert ytm["calls"] == []


def test_channel_artist_survives_a_youtube_music_outage(client, ytm):
    h = prof(client); ch = "UC" + uid() + "abcdefgh"
    t = client.post("/api/tracks/ensure", json=[{"video_id": vid(), "title": "S", "artists": [{"name": "Outage Artist", "ytm_channel_id": ch}]}], headers=h).json()[0]
    p = client.get(f"/api/artists/{t['artists'][0]['id']}", headers=h)  # _yt_artist raises KeyError in the fake: nothing cached for this channel
    assert p.status_code == 200 and p.json()["popular"][0]["track_id"] == t["id"] and p.json()["discography"] == []


def test_follow_and_library(client, ytm):
    h = prof(client); ch = "UC" + uid() + "abcdefgh"
    ytm["artist"][ch] = artist_payload(ch)
    aid = client.get(f"/api/artists/ytm:{ch}", headers=h).json()["id"]
    assert client.put(f"/api/me/follows/{aid}", headers=h).status_code == 204 and client.put(f"/api/me/follows/{aid}", headers=h).status_code == 204
    assert client.get(f"/api/artists/{aid}", headers=h).json()["followed"] is True
    assert [a["id"] for a in client.get("/api/me/library", headers=h).json()["artists"]] == [aid]
    assert client.get("/api/me/library", headers=prof(client)).json()["artists"] == []  # per profile
    assert client.delete(f"/api/me/follows/{aid}", headers=h).status_code == 204 and client.get("/api/me/library", headers=h).json()["artists"] == []
    assert client.put("/api/me/follows/99999999", headers=h).status_code == 404 and client.put(f"/api/me/follows/{aid}").status_code == 401


def test_album_by_ytm_id_marks_library_tracks_and_saves(client, ytm):
    h = prof(client); ch = "UC" + uid() + "abcdefgh"; b = "MPREb_" + uid(); v1, v2 = vid(), vid()
    ytm["album"][b] = album_payload("Brand Album", "Album Artist", ch, "2024", tracks=[(v1, "Opening"), (v2, "Closing")])
    client.post("/api/tracks/ensure", json=[{"video_id": v2, "title": "Closing", "artists": [{"name": "Album Artist", "ytm_channel_id": ch}]}], headers=h)
    a = client.get(f"/api/albums/ytm:{b}", headers=h).json()
    assert isinstance(a["id"], int) and a["title"] == "Brand Album" and a["year"] == 2024 and a["ytm_browse_id"] == b and a["artist"]["name"] == "Album Artist" and a["saved"] is False
    assert [(t["video_id"], t["track_number"], t["in_library"]) for t in a["tracks"]] == [(v1, 1, False), (v2, 2, True)]
    assert a["tracks"][0]["album"] == {"title": "Brand Album", "ytm_browse_id": b} and a["tracks"][0]["artists"][0]["ytm_channel_id"] == ch and a["tracks"][0]["duration_ms"] == 200_000
    assert client.get(f"/api/albums/{a['id']}", headers=h).json()["id"] == a["id"] and client.get(f"/api/art/album/{a['id']}").status_code == 200
    assert run(lambda c: c.fetchval("SELECT count(*) FROM albums WHERE ytm_browse_id = $1", b)) == 1
    assert client.put(f"/api/me/albums/{a['id']}", headers=h).status_code == 204
    assert client.get(f"/api/albums/{a['id']}", headers=h).json()["saved"] is True
    lib = client.get("/api/me/library", headers=h).json()["albums"]
    assert [(x["id"], x["title"], x["artist"]["name"], x["year"]) for x in lib] == [(a["id"], "Brand Album", "Album Artist", 2024)]
    assert client.delete(f"/api/me/albums/{a['id']}", headers=h).status_code == 204 and client.get("/api/me/library", headers=h).json()["albums"] == []
    assert client.put("/api/me/albums/99999999", headers=h).status_code == 404
    # ensuring a track from this page ties it to the album row
    t = client.post("/api/tracks/ensure", json=[{k: a["tracks"][0][k] for k in ("video_id", "title", "artists", "album", "duration_ms")}], headers=h).json()[0]
    assert t["album"]["id"] == a["id"]


def test_local_album_lists_its_tracks(client, ytm):
    h = prof(client); t = mk_track(client, h)
    alb = run(lambda c: c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ('Home Album', 'home album', $1) RETURNING id", t["artists"][0]["id"]))
    run(lambda c: c.execute("UPDATE tracks SET album_id = $2 WHERE id = $1", t["id"], alb))
    a = client.get(f"/api/albums/{alb}", headers=h).json()
    assert [(x["track_id"], x["track_number"], x["in_library"]) for x in a["tracks"]] == [(t["id"], 1, True)] and a["artist"]["id"] == t["artists"][0]["id"]


def test_id_forms_and_errors(client, ytm):
    h = prof(client)
    assert client.get("/api/artists/abc", headers=h).status_code == 422 and client.get("/api/artists/ytm:bad id", headers=h).status_code == 422
    assert client.get("/api/artists/99999999", headers=h).status_code == 404 and client.get("/api/albums/99999999", headers=h).status_code == 404
    assert client.get("/api/albums/xyz", headers=h).status_code == 422
    assert client.get("/api/artists/ytm:UCnotinthefake12345", headers=h).status_code == 502   # YouTube Music failed and we have no row
    assert client.get("/api/albums/ytm:MPREb_notinthefake", headers=h).status_code == 502
    assert client.get("/api/artists/1").status_code == 401


def test_search_tabs_list_youtube_artists_and_albums(client, ytm):
    h = prof(client); q = "zzq" + uid(); ch = "UC" + uid() + "abcdefgh"; b = "MPREb_" + uid()
    ytm["search"][("artists", q)] = [{"resultType": "artist", "artist": "Remote Artist", "browseId": ch, "thumbnails": thumbs()}]
    ytm["search"][("albums", q)] = [{"resultType": "album", "title": "Remote Album", "browseId": b, "year": "2023", "artists": [{"name": "Remote Artist", "id": ch}], "thumbnails": thumbs()}]
    r = client.get("/api/search", params={"q": q, "type": "artists"}, headers=h).json()
    assert r["artists"] == [] and r["youtube_artists"] == [{"id": None, "name": "Remote Artist", "ytm_channel_id": ch, "thumb_url": "https://img/large"}] and r["youtube_albums"] == []
    r = client.get("/api/search", params={"q": q, "type": "albums"}, headers=h).json()
    assert r["youtube_albums"] == [{"id": None, "title": "Remote Album", "ytm_browse_id": b, "year": 2023, "artist": "Remote Artist", "thumb_url": "https://img/large"}]
    for kw in ({"remote": "never"}, {"type": "songs"}, {"type": "all"}):
        got = client.get("/api/search", params={"q": q, **{"type": "artists", **kw}}, headers=h).json()
        assert got["youtube_artists"] == [] and got["youtube_albums"] == []
    n = len(ytm["calls"]); client.get("/api/search", params={"q": q, "type": "artists"}, headers=h)
    assert len(ytm["calls"]) == n  # cached
    ytm["artist"][ch] = artist_payload(ch, name="Remote Artist")  # once the artist is in the database the row carries its id
    aid = client.get(f"/api/artists/ytm:{ch}", headers=h).json()["id"]
    assert client.get("/api/search", params={"q": q, "type": "artists"}, headers=h).json()["youtube_artists"][0]["id"] == aid
    # a library match is listed first; YouTube is only added on request
    local = client.get("/api/search", params={"q": "remote artist", "type": "artists"}, headers=h).json()
    assert local["artists"] and local["youtube_artists"] == []
    ytm["search"][("artists", "remote artist")] = []
    assert client.get("/api/search", params={"q": "remote artist", "type": "artists", "remote": "always"}, headers=h).json()["youtube_artists"] == []
    assert ("artists", "remote artist") in ytm["calls"]

import uuid


def mk(client, name=None):
    r = client.post("/api/profiles", json={"name": name or f"p-{uuid.uuid4().hex[:8]}", "avatar": "🎧"})
    assert r.status_code == 201
    return r.json()


def test_no_profile_is_401(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-Profile-Id": "999999"}).status_code == 401
    assert client.get("/api/me", headers={"X-Profile-Id": "abc"}).status_code == 401


def test_header_and_query_param_both_work(client):
    p = mk(client)
    assert client.get("/api/me", headers={"X-Profile-Id": str(p["id"])}).json()["name"] == p["name"]
    assert client.get(f"/api/me?profile={p['id']}").json()["id"] == p["id"]


def test_list_needs_no_profile_and_duplicate_name_409(client):
    p = mk(client)
    assert any(x["id"] == p["id"] for x in client.get("/api/profiles").json())
    assert client.post("/api/profiles", json={"name": p["name"]}).status_code == 409
    assert client.post("/api/profiles", json={"name": ""}).status_code == 422


def test_patch_merges_prefs_shallowly(client):
    p = mk(client); h = {"X-Profile-Id": str(p["id"])}
    client.patch("/api/me", json={"prefs": {"theme": "dark", "crossfade_s": 4}}, headers=h)
    r = client.patch("/api/me", json={"prefs": {"theme": "light"}, "avatar": "🎹"}, headers=h).json()
    assert r["prefs"] == {"theme": "light", "crossfade_s": 4} and r["avatar"] == "🎹" and r["name"] == p["name"]

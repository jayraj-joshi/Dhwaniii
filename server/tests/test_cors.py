"""The web app runs on another subdomain: only origins in CORS_ORIGINS may call the API from a browser."""
APP = "https://app.example.com"


def test_preflight_allowed_for_the_web_app(client):
    r = client.options("/api/me/likes/1", headers={"Origin": APP, "Access-Control-Request-Method": "PUT",
                                                   "Access-Control-Request-Headers": "x-profile-id,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP and "PUT" in r.headers["access-control-allow-methods"]


def test_other_origins_get_no_cors_headers(client):
    r = client.options("/api/profiles", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in client.get("/api/profiles", headers={"Origin": "https://evil.example"}).headers


def test_audio_headers_are_readable_cross_origin(client):
    r = client.get("/api/profiles", headers={"Origin": APP})
    assert r.headers["access-control-allow-origin"] == APP
    assert "content-range" in r.headers["access-control-expose-headers"].lower()

"""The plain HTML/JS test console is served by the API (same origin, so no CORS) and stays free of innerHTML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "static" / "console"


def test_console_is_served_without_a_profile(client):
    r = client.get("/console/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Resonance" in r.text
    assert client.get("/console").status_code in (200, 307)                       # redirects to the trailing slash
    for name, kind in [("app.js", "javascript"), ("style.css", "css")]:
        assert client.get(f"/console/{name}").status_code == 200 and kind in client.get(f"/console/{name}").headers["content-type"]
    assert client.get("/console/missing.js").status_code == 404
    assert client.get("/console/../main.py").status_code in (404, 400)           # no path traversal out of the folder


def test_console_never_writes_html_from_data():
    js = (ROOT / "app.js").read_text()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", js)  # titles come from YouTube: text only
    for needed in ("/api/search", "/api/stream/", "/api/import", "/api/admin/status", "/api/me/wrapped", "type: \"transfer\""):
        assert needed in js

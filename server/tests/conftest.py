"""Fixtures: a throwaway `resonance_test` database (created on demand, migrations applied) and an API client."""
import asyncio
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import config

TEST_DB = "resonance_test"
FAKE_REMOTE: list[dict] = []  # tests append ranked candidates here; search never touches YouTube


def _url(db: str) -> str:
    u = urlsplit(config.DATABASE_URL)
    return urlunsplit(u._replace(path="/" + db))


async def _recreate():
    admin = await asyncpg.connect(_url("postgres"))
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()


@pytest.fixture(scope="session")
def client():
    asyncio.run(_recreate())
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "DATABASE_URL", _url(TEST_DB))
    mp.setattr(config, "CORS_ORIGINS", ["https://app.example.com"])
    mp.setattr(config, "REDIS_URL",config.REDIS_URL.rsplit("/", 1)[0] + "/9")
    from app import youtube
    async def no_network(redis, nq, merge=False):
        return list(FAKE_REMOTE)
    mp.setattr(youtube, "search", no_network)
    from app.routes import discovery
    def offline(*a, **k):
        raise RuntimeError("YouTube Music is not reachable in tests")
    mp.setattr(discovery, "_yt_artist", offline)
    mp.setattr(discovery, "_yt_album", offline)
    mp.setattr(discovery, "_yt_search_kind", lambda q, kind: [])
    import httpx
    from app import autoplay
    def offline_http():  # autoplay's Tavily / NVIDIA calls: a test that forgets to mock them gets a 503, never the real service
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"detail": "offline in tests"})))
    mp.setattr(autoplay, "make_client", offline_http)
    mp.setattr(youtube, "_ytm_search", lambda q: [])  # enrichment is a no-op unless a test supplies candidates
    mp.setattr(youtube, "_ytm_suggest", lambda q: [])
    async def no_warmup():
        pass
    mp.setattr(youtube, "warm_up", no_warmup)
    from app.main import app  # imported late so the patched config is what lifespan reads
    with TestClient(app) as c:
        c.portal.call(c.app.state.redis.flushdb)  # cached YouTube Music pages from an earlier run must not leak in
        yield c
    mp.undo()

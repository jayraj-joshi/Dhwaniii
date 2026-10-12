"""Synthetic library for timing: `python -m app.tools.seed --n 10000` (or `--clear` to remove it).
Seeded rows are recognisable: sources.video_id starts with 'seed', artists/albums carry a 'seed…' ytm id."""
import argparse
import asyncio
import random

from .. import db
from ..text import normalize
from ..tracks import refresh_track_search

SYL = [c + v for c in "bdfghjklmnprstvwyz" for v in "aeiou"] + ["an", "en", "ar", "el", "on", "in", "sh", "ch"]


def word(r: random.Random) -> str:
    return "".join(r.choice(SYL) for _ in range(r.choice((2, 2, 3, 3, 4)))).capitalize()


async def seed(pool, n: int, rng_seed: int = 1) -> None:
    r = random.Random(rng_seed)
    vocab = [word(r) for _ in range(5000)]
    pick = lambda: vocab[min(int(r.expovariate(1 / 600)), len(vocab) - 1)]  # a few hundred common words, a long tail
    async with pool.acquire() as c:
        na, nb = max(10, n // 10), max(10, n // 2)
        names = [f"{pick()} {pick()}" for _ in range(na)]
        art_ids = [x["id"] for x in await c.fetch(
            "INSERT INTO artists (name, name_norm, ytm_channel_id) SELECT * FROM unnest($1::text[], $2::text[], $3::text[]) RETURNING id",
            names, [normalize(x) for x in names], [f"seedA{i:06d}" for i in range(na)])]
        a_of_album = [r.randrange(na) for _ in range(nb)]
        titles = [" ".join(pick() for _ in range(r.choice((1, 2, 3)))) for _ in range(nb)]
        alb_ids = [x["id"] for x in await c.fetch(
            "INSERT INTO albums (title, title_norm, artist_id, ytm_browse_id) SELECT * FROM unnest($1::text[], $2::text[], $3::bigint[], $4::text[]) RETURNING id",
            titles, [normalize(t) for t in titles], [art_ids[i] for i in a_of_album], [f"seedB{i:06d}" for i in range(nb)])]
        k_of = [r.randrange(nb) for _ in range(n)]
        tt = [" ".join(pick() for _ in range(r.choice((1, 2, 3, 4)))) for _ in range(n)]
        tr_ids = [x["id"] for x in await c.fetch(
            "INSERT INTO tracks (title, title_norm, primary_artist_id, album_id, duration_ms) "
            "SELECT * FROM unnest($1::text[], $2::text[], $3::bigint[], $4::bigint[], $5::int[]) RETURNING id",
            tt, [normalize(t) for t in tt], [art_ids[a_of_album[k]] for k in k_of], [alb_ids[k] for k in k_of],
            [r.randint(120_000, 400_000) for _ in range(n)])]
        await c.execute("INSERT INTO track_artists (track_id, artist_id, role) SELECT t.id, t.primary_artist_id, 'primary' FROM tracks t WHERE t.id = ANY($1)", tr_ids)
        await c.execute("INSERT INTO sources (track_id, video_id) SELECT * FROM unnest($1::bigint[], $2::text[])",
                        tr_ids, [f"seed{i:07d}" for i in range(n)])
    for i in range(0, n, 2000):
        await refresh_track_search(pool, tr_ids[i:i + 2000])


async def clear(pool) -> None:
    await pool.execute("DELETE FROM tracks WHERE id IN (SELECT track_id FROM sources WHERE video_id LIKE 'seed%')")
    await pool.execute("DELETE FROM albums WHERE ytm_browse_id LIKE 'seedB%'")
    await pool.execute("DELETE FROM artists WHERE ytm_channel_id LIKE 'seedA%'")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000)
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    pool = await db.create_pool()
    await db.migrate(pool)
    await clear(pool) if a.clear else await seed(pool, a.n)
    print("cleared" if a.clear else f"seeded {a.n} tracks")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

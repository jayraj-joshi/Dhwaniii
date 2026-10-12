"""Enrich tracks downloaded before enrichment existed: `python -m app.tools.reenrich [--limit N]`.
Walks provisional tracks that have a file, enriches them and rewrites the file's tags."""
import argparse
import asyncio

from .. import db, enrich, storage


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many tracks (0 = all)")
    a = ap.parse_args()
    pool = await db.create_pool()
    await db.migrate(pool)
    rows = await pool.fetch(
        "SELECT t.id, f.path FROM tracks t JOIN sources s ON s.track_id = t.id JOIN files f ON f.source_id = s.id "
        "WHERE t.metadata_status = 'provisional' ORDER BY t.id" + (f" LIMIT {a.limit}" if a.limit else ""))
    done = 0
    for r in rows:
        if await enrich.enrich_track(pool, r["id"]):
            done += 1
            if (path := storage.abs_path(r["path"])).exists():
                await enrich.write_file_tags(pool, r["id"], path)
        await asyncio.sleep(0.5)  # be gentle with YouTube Music
    print(f"enriched {done} of {len(rows)} provisional tracks")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

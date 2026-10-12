This file describes the Resonance folder and file structure.
Add what each directory contains and what each file is for below.

Last updated: 2026-10-12 (M0.1)

```
Resonance/
├── .env                      # host-dev config values (gitignored); compose overrides container-specific ones
├── .gitignore                # ignores .env, audio/, cookies/, .venv/, node_modules/, dist/, spikes/, scratch/, backups/
├── docker-compose.yml        # db (postgres:16) + redis (7) today; api/worker/web added in M1.1 / M1.11
├── planning.md               # the implementation plan: decisions D1–D47, milestones, specs. Source of truth.
├── structure.md              # this file: what lives where
├── tracking.md               # progress log, spike findings, how to resume
├── scripts/backup.sh         # M4.5: nightly pg_dump (+ optional rsync of audio/), keeps 14
├── db/init/01-extensions.sql # pg_trgm + unaccent; runs only on a fresh Postgres volume
├── server/                   # Python API + arq worker (one image runs both: D9)
│   ├── pyproject.toml        # closed dependency list (D2); uv.lock is committed
│   ├── Dockerfile            # python:3.12-slim + ffmpeg + deno + uv, non-root user `app`
│   ├── .dockerignore
│   ├── migrations/
│   │   ├── 001_core.sql      # Phase 1 schema (planning.md §5.1): profiles, artists, albums, tracks, sources, files, playlists, likes, history, jobs, devices, playback_state
│   │   ├── 002_recent_searches.sql  # M2.5-B
│   │   ├── 003_discovery.sql        # M3.1-B: follows, saved_albums
│   │   ├── 004_imports.sql          # M3.4-B: imports
│   │   └── 005_device_downloads.sql # M4.2-B: device_downloads
│   ├── app/
│   │   ├── config.py         # the ONLY env reader (D5)
│   │   ├── db.py             # asyncpg pool + migration runner under pg_advisory_lock (D4)
│   │   ├── main.py           # FastAPI app, lifespan (pg, redis, arq pool), CORS for the web app's origin, GET /api/health
│   │   ├── static/console/   # test console: index.html, style.css, app.js (plain JS, served at /console/)
│   │   ├── routes/
│   │   │   ├── stream.py     # GET /api/stream/{video_id} (disk, else live proxy), POST /api/tracks/{video_id}/cache
│   │   │   ├── stats.py      # M5.5-B: /api/me/stats, /api/me/wrapped (computed from play_history)
│   │   │   ├── autoplay.py       # D51: autoplay_extend job (Tavily -> LLM -> YouTube Music -> ensure_track -> autoplay_tracks event)
│   │   ├── admin.py      # M4.4-B: /api/admin/status, jobs retry/alternative, ytdlp update
│   │   │   ├── imports.py    # M3.4-B: POST /api/import, GET /api/imports/{id}
│   │   │   ├── home.py       # M3.3-B: GET /api/home
│   │   │   ├── discovery.py  # M3.1-B: /api/artists/*, /api/albums/*, follows, saved albums, YouTube artist/album search results
│   │   │   ├── playlists.py  # /api/playlists/* (CRUD, cover, add/remove/move); PLAYLIST_SELECT + playlist_json shared with search
│   │   │   ├── library.py    # /api/me/history, likes, library, recent
│   │   │   ├── tracks.py     # POST /api/tracks/ensure, GET /api/tracks/{id}, GET /api/art/{kind}/{id}
│   │   │   ├── search.py     # /api/search + /api/suggest, scoring SQL (§9.3)
│   │   │   └── profiles.py   # current_profile dependency (D10), /api/profiles, /api/me, /api/settings
│   │   ├── importer.py       # M3.4-B: CSV parsing, playlist URL check, run_import job
│   │   ├── autoplay.py       # D51: autoplay_extend job (Tavily -> LLM -> YouTube Music -> ensure_track -> autoplay_tracks event)
│   │   ├── admin.py          # M4.4-B: update yt-dlp in this virtualenv, then exit so Docker restarts the process
│   │   ├── enrich.py         # M2.1: enrich_track (ytmusicapi match), merge_artists, album cover, write_file_tags
│   │   ├── youtube.py        # ytmusicapi/yt-dlp search, rank_candidates (§9.4), ys: cache, resolve_audio_url (su: cache)
│   │   ├── tools/reenrich.py # `python -m app.tools.reenrich`: enrich tracks downloaded before M2.1 and rewrite their tags
│   │   ├── tools/seed.py     # `python -m app.tools.seed --n 10000` synthetic library for timing (`--clear` removes it)
│   │   ├── tracks.py         # ensure_track (D13), refresh_track_search (D23), Track serializer SQL (D14/D15), request_download (D27)
│   │   ├── storage.py        # audio-store paths (D16), loudness, 600x600 art crop, mutagen tags, LRU eviction (D33)
│   │   ├── text.py           # normalize, normalize_query, parse_title (§9.1, §9.2)
│   │   ├── worker.py         # arq: download_track (§9.5), feed_backlog cron, evict cron; runs migrations on startup
│   │   └── ws.py             # WebSocket hub + GET /ws (§8): Redis pub/sub fan-out (D39), devices, last-play-wins (D35), publish helpers
│   └── tests/
│       ├── conftest.py       # `client` fixture: fresh resonance_test DB + TestClient
│       ├── test_text.py      # M1.2 required cases (21 tests)
│       ├── test_profiles.py  # M1.3
│       ├── search_fixture.json   # 42 tracks + 35 expected hit/miss/rank-1 queries (M1.4 calibration)
│       ├── helpers.py            # run(), prof(), mk_track(), jobs() for API tests
│       ├── test_autoplay.py      # D51: trigger, parsing, the job, failures, isolation (all offline)
│       ├── test_cors.py          # CORS: the web app's origin is allowed, others are not
│       ├── test_console.py       # the console is served; no innerHTML-style calls in app.js
│       ├── test_stats.py         # M5.5-B
│       ├── test_admin.py         # M4.4-B
│       ├── test_offline.py       # M4.2-B: offline marks pin files against eviction
│       ├── test_isolation.py     # M4.3-B: profile B sees nothing of profile A
│       ├── test_import.py        # M3.4-B
│       ├── test_home.py          # M3.3-B
│       ├── test_discovery.py     # M3.1-B (YouTube Music faked with real-shaped payloads)
│       ├── test_fix_metadata.py  # M2.3-B: PATCH, candidates, switching source
│       ├── test_enrich.py        # M2.1: matching rules, artist unification, album cover, merge_artists, worker hook
│       ├── test_history.py       # M1.13-B
│       ├── test_library.py       # M1.14-B likes/library/recent + M1.15-B settings
│       ├── test_playlists.py     # M1.14-B
│       ├── test_ws.py            # M1.9: two sockets per profile, relay rules, isolation, ws:all broadcast
│       ├── test_worker.py        # M1.8: pipeline (real ffmpeg/mutagen, fake yt-dlp step), retries, eviction, backlog
│       ├── test_stream.py        # M1.7: Range from disk, live proxy (mocked upstream), one job under concurrency, cache endpoint
│       ├── test_tracks.py        # M1.6: ensure idempotence/concurrency, validation, art
│       ├── test_search_sql.py    # runs the fixture through the real API
│       ├── test_search_remote.py # youtube[] merge: in_library, remote modes, de-duplication (network stubbed)
│       └── test_rank.py          # rank_candidates order and penalties
├── web/                      # the frontend: a PWA served as static files from its own subdomain (VITE_API_URL is baked in at build time)
│   ├── package.json, tsconfig.json, vite.config.ts, index.html   # Vite + React 19 + TS; vite-plugin-pwa makes the manifest and service worker
│   ├── Dockerfile, nginx.conf, .dockerignore   # node build stage, then nginx: static files, SPA fallback, no-cache for sw.js and the manifest
│   ├── .env.development, .env.example          # VITE_API_URL (dev points at http://localhost:8000, so dev is cross-origin like production)
│   ├── public/               # icon.svg, icon-192/512.png, icon-maskable-512.png, apple-touch-icon.png (rings motif)
│   └── src/
│       ├── main.tsx, App.tsx, styles.css, types.ts   # App: profile gate, session (engine + socket), routes, keyboard shortcuts
│       ├── lib/              # api (fetch, ?profile=, stream/ws URLs), session, tracks (cache + live overrides), ws, theme, device, install, toast, ui, imports, format
│       ├── player/           # queue.ts (pure reducer, §10.3) + queue.test.ts, engine.ts (audio element, Media Session, history, WS sync, autoplay D51), hooks.ts
│       ├── components/       # Cover, StringBar (the progress bar is a plucked string), TrackRow (+ download state), Sheets (track menu, playlist picker, queue), Player (mini, desktop bar, full-screen), Nav, Hero, Common, Icon, Sheet, Toasts
│       └── pages/            # Profiles, Home, Search, Library, Liked, Playlist, Album, Artist, Settings, Import
├── audio/                    # AUDIO_DIR: downloaded audio + artwork (gitignored; created at runtime)
└── cookies/                  # cookies.txt for yt-dlp (gitignored; optional)
```

## Per-file notes

- `.env` holds every variable listed in planning.md §3. `LASTFM_*` and `S3_*` are unused (D43, D47).

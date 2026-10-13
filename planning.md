This file breaks the Resonance design document into milestones and an implementation sequence.
Add milestones and their order below as planning happens.

# Resonance: Implementation Plan

Plan version 1, written 2026-10-12. It is based on `Resonance_Design_Document.pdf` v1.0 (October 2026).

---

## 0. How to use this file

- The **design doc** says *what* the product is and *why*. **This file** says *how* it is built, *in what order*, and records every decision the doc leaves open or gets wrong. **When the two disagree, this file wins.** Section 2 lists each deviation and the reason for it.
- References: "doc §5.4" means a section of the design doc. "D12" means decision 12 in §2 of this file. "M1.7" means milestone 1.7 in §11.
- **To change a decision**, edit its entry in §2 and append `(changed YYYY-MM-DD: reason)`. Code must never quietly contradict this file.
- **Progress** is recorded in `tracking.md`, and the **actual folder and file layout** in `structure.md`. Update both at the end of every milestone. Edit this file only when the plan itself changes.
- **This file is self-contained.** Everything needed to build Resonance is here. The PDF is not required, and a `doc §` reference only records where an idea came from. Appendix A lists every feature in the design doc and Appendix B every screen, each mapped to the milestone that builds it.
- **Build only the current milestone.** Don't scaffold anything for later phases. Each milestone lists its own tables, endpoints and files.
- Each milestone is done when its acceptance criteria pass, its listed tests pass, and `tracking.md` and `structure.md` are updated.
- **Backend first (changed 2026-10-12, D48).** Only the server is being built now. The browser client is a separate, later track. Milestones are split into **`-B`** (backend part, in scope) and **`-F`** (client part, deferred). A milestone with no suffix is single-track. Every `-F` item stays in this file as the spec for the frontend track.

---

## 1. Scope recap

Resonance is a self-hosted, browser-only, Spotify-like music app for one person or a household. Music comes from YouTube and YouTube Music through yt-dlp. **Search always checks the local library first.** On a miss, the app finds the track on YouTube, starts a live proxy stream within about 2 s, and downloads a tagged copy in the background. The next play then comes from disk.

**The core loop, which must work before anything else:** type a song, hear it within 2 s, close the app, search again, and it plays instantly from the library (doc §13).

**Goals**
- Spotify parity for everything that makes sense in a personal library (Appendix A).
- Time to first sound under 300 ms for cached tracks and under 2.5 s for uncached ones.
- Zero duplicate downloads: the same video is never fetched twice, even when two devices request it at once.
- Clean metadata. Titles, artists, albums and artwork should look like a real catalogue, not raw YouTube titles.
- Browser only: desktop and phone browsers, installable as a PWA. No native desktop or mobile app.
- Runs on modest hardware (a Raspberry Pi 5, a small VPS or a spare laptop), deployed with one `docker-compose.yml`.

**Non-goals**
- Public hosting, sharing music with strangers, or any commercial use.
- Competing with Spotify's licensed catalogue, or offering podcasts and audiobooks.
- DRM-protected sources. The app only uses what yt-dlp can access as a normal viewer.

### Phase map

| Phase | Theme | Contents (summary) | Doc estimate |
|---|---|---|---|
| 0 | Foundations | Repo setup, tool spike to verify every external dependency | 1–2 days |
| 1A | Core loop | Search (local + YouTube), stream (disk + live proxy), download worker, WebSocket `track_cached`, bare UI | — |
| 1B | Phase 1 completion | Profiles, queue, shuffle/repeat, likes, playlists, history/recently played, Media Session, shortcuts, theme, eviction, settings. **Every P0 item.** | 1A+1B: 2–3 weeks |
| 2 | Polish | YouTube Music enrichment, artwork, lyrics, Web Audio engine (gapless, crossfade, normalization, EQ), sleep timer, speed, recent searches, fix-metadata, PWA install, full-screen player | 2 weeks |
| 3 | Discovery | Radio, autoplay, home feed, artist/album pages, follow/save, playlist import | 2 weeks |
| 4 | Devices | Connect-style transfer, offline downloads, household profile polish, admin page, backups | 2 weeks |
| 5 | Extras | Daily Mixes, Discover Weekly, Release Radar, stats/Wrapped, genre and mood browsing, collaborative playlists, folders, Chromecast, animated background, optional MinIO | ongoing |
| 6 | Later | Authentication, access control, remote access | TBD |

**Scope note (D48):** the phases below describe the whole product. Right now only their server (`-B`) parts are built; client (`-F`) parts wait for the frontend track.

**Priority rule (resolves the doc's priority table vs. roadmap):** every **P0** feature ships by the end of Phase 1. **P1** features ship in Phases 2–4, and **P2** features in Phase 5. Where the roadmap (doc §13) and the priority table (doc §2) disagree, this rule decides. §2 D40 shows where each case lands, and Appendix A gives every feature's priority and milestone.

---

## 2. Decisions (binding)

### A. Infrastructure and process

**D1. Monorepo layout.** Top level holds `server/` (Python API and worker), `web/` (React client), the existing `db/init/`, `audio/` (audio store) and `cookies/`. `audio/`, `cookies/`, `.env` and build output are gitignored. Compose service names and commands follow doc §11: `build: ./server`, `uvicorn app.main:app`, `arq app.worker.Settings`, `build: ./web`.

**D2. Backend toolchain (closed list).** Python 3.12 (installed on the host as `python3.12`), managed with **uv** (`pyproject.toml` + `uv.lock`). Runtime dependencies are `fastapi`, `uvicorn[standard]`, `asyncpg`, `redis` (asyncio client), `arq`, `httpx`, `yt-dlp[default]` (pulls in `mutagen` and the JS challenge solver), `ytmusicapi` and `python-multipart`. The only dev dependency is `pytest`; async tests call `asyncio.run` inside plain test functions. **No ORM, no pydantic-settings, no Pillow.** Adding any dependency means updating this list first.

**D3. Frontend toolchain (closed list).** *(changed 2026-10-12: built with React, TypeScript, Vite, react-router, Zustand, TanStack Query, vite-plugin-pwa and vitest. Tailwind was dropped for hand-written CSS with design tokens, and `@dnd-kit` is unused: playlist reordering is Move to top / Move to bottom in the track menu.)*  React + TypeScript + Vite, Tailwind, Zustand and TanStack Query (all from doc §11), plus:
- `react-router` (library mode) for routing.
- `@dnd-kit/core` and `@dnd-kit/sortable` for drag-and-drop. Native HTML5 drag-and-drop doesn't work on phones.
- `vite-plugin-pwa`, from Phase 2.
- `idb-keyval`, from Phase 4.
- `vitest` for tests.

Colour extraction is a small hand-written canvas function, not a library. Adding any dependency means updating this list first.

**D4. Database access and migrations.** `asyncpg` pool with raw SQL, matching the doc's `db.fetch(...)` style. SQL lives in the module that uses it.
- Migrations are numbered plain SQL files, `server/migrations/NNN_name.sql`. A small runner in `app/db.py` applies them at **both API and worker startup**, inside `pg_advisory_lock` so the two processes can't race, and records each one in `schema_migrations(version, applied_at)`.
- Never edit an applied migration. Add a new one.
- `001` also runs `CREATE EXTENSION IF NOT EXISTS pg_trgm` and `unaccent`. `db/init/01-extensions.sql` only runs on a fresh volume, so `001` covers databases created any other way. Keep the init file as it is.

**D5. Configuration.** `app/config.py` is the only module that reads environment variables (`os.environ`, with defaults). `.env` holds **host-dev values** (`localhost`, `./audio`). Docker Compose `environment:` entries **override** the values that differ inside containers. §3 has the full table.

**D6. Same origin everywhere.** *(changed 2026-10-12: the web app and the API are on two subdomains. The API now allows the web app's origin with `CORS_ORIGINS` (config.py, CORSMiddleware, exposes `Content-Range`). The client sends the profile as `?profile=` on every call (D10), so GETs need no CORS preflight; `<audio>` and the socket go straight to the API, and artwork is fetched with CORS so the service worker can cache it. The nginx `/api` and `/ws` proxy below is no longer used: the `web` image only serves static files. The Web Audio caveat below still applies if M2.4 is built: audio would then need CORS headers on `/api/stream`.)*  The `web` container (nginx) serves the static build **and** proxies `/api/` and `/ws` to `api:8000`. In development, the Vite dev server proxies the same paths to `localhost:8000`. This means no CORS anywhere, which is required for Web Audio `MediaElementSource` (cross-origin audio plays silent without CORS) and keeps the service worker simple. nginx settings:
- `proxy_buffering off` for `/api/stream/`
- WebSocket upgrade headers for `/ws`
- `client_max_body_size 10m`

**D7. One worker container.** The doc's compose outline sets `replicas: 2`, but its operational notes say to limit downloads to **two concurrent**, and 2 replicas × 2 jobs would be 4. **Resolution:** one `worker` service with arq `max_jobs = WORKER_MAX_JOBS` (2).

**D8. Cookies.** `./cookies` is mounted read-only into **both** `api` and `worker`. The API's stream-URL resolution hits the same age and region restrictions as downloads, but the doc only mounts cookies into the worker. The cookie file is passed to yt-dlp only if it exists.

**D9. Server image.** One image is used for both `api` and `worker`:
- Base `python:3.12-slim`.
- `ffmpeg` installed from apt.
- **Deno** copied from the official `denoland/deno` binary image. yt-dlp needs a JS runtime for YouTube (doc §11).
- Dependencies installed with uv; runs as a non-root user.

The web image is a Node build stage followed by an `nginx:alpine` stage. Pin exact base-image versions at implementation time. The API runs as **a single Uvicorn process** (see D39).

### B. Identity and data

**D10. Profile resolution.** A single FastAPI dependency, `current_profile`, reads the `X-Profile-Id` header and **falls back to a `?profile=` query parameter**. The fallback is needed because `<audio src>`, `navigator.sendBeacon` and browser WebSockets can't set headers.
- No other code reads the profile.
- A missing or unknown profile returns 401, and the client shows the profile picker.
- Artwork endpoints don't require a profile.
- Authentication (Phase 6) replaces this one dependency.

**D11. IDs, units and time.** All database IDs are `bigint generated always as identity` and are numbers in JSON. All durations and positions are **milliseconds** (`duration_ms`, `position_ms`, `ms_played`). Timestamps are `timestamptz`, sent as ISO-8601 UTC strings in JSON.

**D12. Validate `video_id` at every entry point.** Video IDs reach yt-dlp and filesystem paths, so check them against `^[A-Za-z0-9_-]{11}$` wherever one enters (stream, cache, ensure, import, sources), and return 422 otherwise. This prevents path traversal and argument injection.

**D13. Provisional tracks (deviation).** The doc inserts a track row only when the download finishes (doc §4.1 step 7). That doesn't work, because the queue, likes, playlists, history and imports all key on `track_id` (doc §7 and the WebSocket messages), and an import must build its playlist before its downloads finish.
- **Resolution:** when the user acts on a "Not in library" result (play, queue, like, add to playlist, cache, import, or a radio batch), the client calls `POST /api/tracks/ensure` with the result's metadata. The server creates the artist, track and source rows with `metadata_status = 'provisional'`, or returns the existing ones.
- The worker later **updates** these rows with better metadata. The step "the track row is inserted" becomes "the track row is upgraded and a `files` row is inserted".
- Searching alone never creates rows.

**D14. `cache_state` is derived, never stored.** The API field takes one of four values, evaluated in this order:

| Value | Condition | UI (doc §10 download indicator) |
|---|---|---|
| `cached` | The preferred source has a `files` row | No icon |
| `downloading` | `download_jobs.status` is `queued` or `running` | Spinning ring, with % once progress events arrive |
| `failed` | `download_jobs.status = 'failed'` | Warning icon. Playing still works through the live proxy. |
| `remote` | None of the above | Cloud icon |

**D15. Preferred source.** A track's preferred source is the source that has a `files` row, or failing that the highest `quality_score`, with ties going to the lowest `id`. API track objects expose its `video_id`, and streaming always uses that `video_id` (doc §5.4).

**D16. Audio store paths.**
- Audio files live at `{video_id[0:2]}/{video_id[2:4]}/{video_id}.{ext}` under `AUDIO_DIR` (doc §3).
- Artwork lives at `art/{kind}/{id}.jpg` under `AUDIO_DIR`, where `kind` is `track`, `album`, `artist` or `playlist`. Track art is keyed by `video_id`; the others by database ID.
- **Every path stored in the database is relative to `AUDIO_DIR`.** Host development uses `./audio` and containers use `/audio`, so absolute paths would break one or the other.

**D17. Pinning is computed (deviation).** The doc stores a `files.pinned` flag. Here, a file counts as pinned when its track is **liked by any profile**, or is in a playlist that some device has marked for offline use (`device_downloads`, Phase 4). The eviction query computes this when it runs. No `pinned` column means nothing can fall out of sync on unlike or playlist removal.

**D18. Playlist rows.** `playlist_tracks` gets its own `id` primary key, because the same track can appear twice and removal or reordering must target one specific row. `position` is `numeric`:

| Operation | New position |
|---|---|
| Append | max + 1 |
| Insert at the start | min − 1 |
| Move between a and b | (a + b) / 2 |

Renumbering isn't needed.

**D19. Saving a track downloads it.** Liking a track, adding it to a playlist or importing it requests a **low**-priority download. Playing a track, the next two items in the queue, and an explicit "cache" action request a **high**-priority download. Opening a playlist pre-caches its first five uncached tracks at low priority (doc §8.2).

**D20. Context strings** are used in `play_history.context`, in "Jump back in" and in `PlaybackState.context`:
- `playlist:<id>`, `album:<id>`, `artist:<id>`, `radio:<track_id>`
- `liked`, `search`, `queue` (the queue context is for tracks started from the queue with no other context)

**D21. Devices.** Each device has a client-generated UUID stored in `localStorage`. Its name defaults to "Browser on OS" (for example "Chrome on macOS") and can be edited in Settings. `devices.id` is that text UUID. The doc's WebSocket examples (`"pixel-8"`) are illustrative only.

### C. Search

**D22. One normalization function, plus a query variant.**
- `app/text.py` defines `normalize(s)`. It is used for **every** stored `*_norm` column **and** for queries.
- `normalize_query(q)` calls `normalize` and then removes noise phrases (doc §4.1).
- SQL never calls `unaccent()`. Accent stripping happens in Python, and the extension stays installed but unused. §9.1 has the exact rules.
- If `normalize` changes, a backfill command must recompute every `*_norm` column.

**D23. Denormalized search columns (deviation).** The doc's SQL (doc §5.3) runs trigram matching on `a.name_norm || ' ' || t.title_norm` across a join. No index can cover that, so it falls back to a sequential scan and misses the 80 ms target. Generated columns also can't reference other tables.
- **Resolution:** `tracks` stores `artist_names_norm`, `album_title_norm` and `artist_title_norm`, maintained by one function, `refresh_track_search(track_id)`. Call it whenever a track, its artists or its album change.
- `search_vec` is a generated tsvector built from these columns.

**D24. The scoring formula is fixed (deviation).** In the doc's formula, `0.55·ts_rank + 0.35·similarity + 0.10·plays`, `ts_rank` is not on a 0–1 scale: a typical match scores about 0.06–0.1. That puts the maximum reachable score near 0.5, so the 0.82 threshold **can never be hit**.
- **Resolution:** each component is normalized to 0–1, keeping the doc's weights. §9.3 has the formula.
- The threshold stays at `SEARCH_HIT_THRESHOLD` (0.82) and gets calibrated in M1.4 using a fixture. Weights may be retuned; record any change here.
- (calibrated 2026-10-12, M1.4: weights 0.55 / 0.35 / 0.10 and the 0.82 threshold held for all 35 fixture queries. Only change: the album joined the `word_similarity` haystack, §9.3. Query SQL must use `$1` directly, not a CTE, or the planner skips the GIN indexes.)

**D25. YouTube search backend (deviation).**
- **Primary:** `ytmusicapi.search(q, filter="songs")`, run in a threadpool. It returns structured artists, album, duration and thumbnails, which gives clean provisional rows. It can also search artists and albums for the search tabs.
- **Fallback:** the doc's flat yt-dlp `ytsearch` (doc §5.1), used when ytmusicapi raises an error or returns nothing.
- `rank_candidates` (§9.4) is applied to the merged list.
- The Phase 0 spike confirms this choice. If ytmusicapi proves unreliable, make the doc's §5.1 approach primary and record that here.

**D26. The lazy "More from YouTube" row** (doc §4 diagram) is served by `GET /api/search?...&remote=always`. The default, `remote=auto`, only queries YouTube on a miss; `remote=never` skips it entirely.

### D. Download and stream

**D27. Download gating and priority (extends doc §4.3).** All download requests go through one function, `request_download(video_id, priority)`:
1. If the preferred source already has a `files` row, return immediately.
2. Upsert the `download_jobs` row: one row per `video_id`, set to `status = 'queued'`, with the priority raised to `high` if requested.
3. For **high** priority: if `SET dl:lock:<video_id> 1 NX EX DOWNLOAD_LOCK_TTL` succeeds, run `arq.enqueue_job("download_track", video_id, _job_id="dl:<video_id>")`.
   - The lock is the doc's gate. arq's `_job_id` stops a job that is still queued or retrying from being duplicated after the lock expires.
   - The worker runs with `keep_result = 0` so the job ID frees up as soon as the job ends. **Verify this arq behaviour in the spike.**
4. For **low** priority, nothing is enqueued. The row waits in a DB backlog. An arq cron, `feed_backlog`, runs every 30 s: when the arq queue (`zcard arq:queue`) has fewer than `WORKER_MAX_JOBS` entries, it moves the oldest low-priority `queued` rows into arq through step 3.
   - A backlog item the user then plays gets promoted to high at once.
   - Large imports trickle in gently, in line with doc §11's "be gentle".

Three layers stop duplicate downloads: the unique `sources.video_id`, the lock plus `_job_id`, and the idempotent job (doc §4.3).

**D28. Retries.** `max_tries = DOWNLOAD_RETRIES + 1` (four attempts in total). The backoff is `Retry(defer = 30 s × 4^(attempt−1))`, giving waits of 30 s, 2 min and 8 min.
- These errors are **permanent**, so the job doesn't retry: yt-dlp messages containing "Video unavailable", "Private video", "removed", or "not available in your country" when no cookies file is present.
- On final failure: set `status = 'failed'`, store the error text, publish `download_failed`, and delete the lock.
- After every job, sleep a random 1–3 s (doc §11).

**D29. Serving from disk.** Use Starlette `FileResponse`, which handles Range requests in current Starlette; confirm this in the spike. There's no custom range code; the doc's `range_file_response` is just `FileResponse`. Media types are `m4a` → `audio/mp4` and `mp3` → `audio/mpeg`. Update `files.last_played` only when the request has no Range header or its range starts at 0, so each play counts once rather than once per chunk.

**D30. Live proxy details (extends doc §5.4).**
- Redis key `su:<video_id>` holds JSON with `url`, `http_headers` (from yt-dlp, which are required) and `mime`, with TTL `STREAM_URL_TTL`.
- Forward the client's Range header, defaulting to `bytes=0-`, and pass through `content-type`, `content-length`, `content-range` and `accept-ranges` along with the upstream status.
- On an upstream 403 or 410, delete `su:`, re-resolve once, and retry.
- Use the format `bestaudio[ext=m4a]/bestaudio`, since m4a works on iOS Safari.
- Direct URLs never go in Postgres (doc §11).
- Never pipe a single yt-dlp process to both the listener and the disk (doc §4.2). A seek or a dropped connection would kill both. The live stream and the background download are always separate.

**D31. Worker pipeline changes (deviation from doc §5.5).**
- **Tags:** yt-dlp only downloads and runs `FFmpegExtractAudio` (remux to `PREF_CODEC`). After metadata is resolved, the worker writes clean tags and the cover **once** with `mutagen`. The doc's `FFmpegMetadata` and `EmbedThumbnail` postprocessors would embed raw YouTube titles, so they're dropped.
- **Signature:** the job is `download_track(ctx, video_id)`. The doc's `profile_id` parameter only routed the notification, and `track_cached` now goes to every profile (D39).

**D32. Loudness.**
- **Measurement:** `ffmpeg -af loudnorm=print_format=json -f null -`. This is the same EBU R128 measurement as `ebur128`, but its JSON output is easier to parse.
- **Storage:** `loudness_lufs = input_i`, and `gain_db = clamp(min(−14 − input_i, −1 − input_tp), −20, +6)`. The target is −14 LUFS, and true peak never goes above −1 dBTP.

**D33. Eviction.** Runs in the worker after every successful download and on an hourly cron.
- **Trigger:** `SUM(files.bytes) > STORAGE_LIMIT_GB`.
- **Order:** unpinned files (D17), least recent `coalesce(last_played, created_at)` first, until usage is at or below 90 % of the limit. For each file, delete it from disk and then delete its row.
- Track and source rows stay, so an evicted track becomes a miss and is fetched again on demand (doc §6).
- If everything is pinned and usage is still over the limit, log a warning and show it on the admin page.

### E. Playback and client

**D34. Queue model (deviation).** The doc's `queue_update {queue: [ids], index}` can't represent Spotify's split between "Next in queue" and "Next from context", or turning shuffle off again (doc §10 Queue). It's replaced by `PlaybackState` (§10.3).
- Queue logic lives **only in the client**, as a pure reducer in `web/src/player/queue.ts`. The server stores the state as JSON in `playback_state` and relays it to the profile's other devices, but never works out the next track itself.
- When a track starts, the client pre-caches the next two items by calling `POST /api/tracks/{video_id}/cache?priority=high`.

**D35. Active device: last play wins (Phase 1).** When a device starts playback it becomes the profile's active device. The server broadcasts `active_device`, and the profile's other devices pause and show "Playing on X". This stops two tabs from overwriting each other's queue. Phase 4 adds explicit transfer and a device picker.

**D36. The audio engine is built in stages.**
- **Phase 1:** a single `HTMLAudioElement` with no Web Audio. This is the most robust option for lock-screen and background playback, especially on iOS.
- **Phase 2:** two `HTMLAudioElement`s → `MediaElementAudioSourceNode` → per-track `GainNode` (normalization and crossfade) → ten `BiquadFilterNode`s (EQ) → master gain → output.
- **Gapless is near-gapless:** the next element starts when the current one reaches its end. The doc's sample-exact gapless (doc §8.1) would mean decoding entire tracks into AudioBuffers, roughly 80 MB per 4-minute track, and AAC encoder padding would spoil it anyway.
- **iOS:** if Web Audio breaks background or lock-screen playback during M2.4 testing, iOS keeps the Phase 1 engine. Negative normalization gain then goes through `element.volume`, and EQ and crossfade are disabled on iOS.

**D37. No per-profile audio quality.** There's one cached file per track. `PREF_CODEC` (m4a by default, mp3 optional; doc §5.5) is server-wide and shown read-only in Settings. Profile `prefs` hold `theme`, `crossfade_s`, `gapless`, `normalize`, `eq`, `autoplay` and `speed`. Volume is per device and lives in `localStorage`.

**D38. Play counting.** `POST /api/me/history` takes an **array** of plays, so offline plays can be sent in a batch (doc §8.4).
- A play is **counted** when `ms_played ≥ min(30 000, 50 % of duration)`.
- `skipped` comes from the client: the user moved on before 30 s.
- `user_track_stats` is upserted in the same transaction: `play_count += counted`, `skip_count += skipped`, and `last_played` is updated.
- The client reports a play when a track ends, is skipped or is replaced, and on `pagehide` through `sendBeacon` (with `?profile=`, per D10).

**D39. WebSocket fan-out.**
- **Redis pub/sub channels:** `ws:all` carries `track_cached`, `download_progress` and `download_failed`, since the audio store is shared by the household. `ws:profile:<id>` carries player and queue state, devices and transfers.
- The API process subscribes to both patterns once and forwards messages to its local sockets. The worker only publishes.
- Connected sockets are held in an in-process map, which is why the API runs as a single process.

### F. Scope

**D40. Where each P0 item lands.** All of these are in Phase 1 (1B), even where doc §13 puts them later:
- theme and keyboard shortcuts
- play history and "Recently played"
- storage limit and LRU eviction
- Settings
- search tabs
- basic profile picker and create

These stay in later phases:
- household profile polish (avatars, profile switching), Phase 4
- the "Downloaded" library filter chip, Phase 4 (it needs offline mode)
- the Artists and Albums filter chips, Phase 3

**D41. Playlist import.**
- **YouTube and YouTube Music playlist URLs:** read with flat yt-dlp extraction; no credentials needed.
- **CSV upload:** Exportify columns `Track Name`, `Artist Name(s)`, `Album Name`, `Duration (ms)`, or generic `title`, `artist`, `album`, `duration_ms`.
- **Pasted Spotify URLs are deferred.** They'd need Spotify API credentials, which aren't in `.env`. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` if that's ever wanted.

**D42. YouTube Music IDs in URLs.** `/api/artists/{id}` accepts a database integer or `ytm:<channelId>`, and `/api/albums/{id}` accepts an integer or `ytm:<browseId>`. A `ytm:` request upserts the artist or album row and returns it with its database `id`; the client then switches to the numeric URL.

**D43. MinIO is deferred to Phase 5.** Until then everything is stored on local disk, and the `S3_*` variables are ignored.

**D44. No authentication until Phase 6** (doc §11). Every endpoint, including admin, is open to anyone who can reach the port. Run it on the home LAN only.

**D45. Artwork.** Every image is a single 600×600 JPEG made with ffmpeg (center crop and scale); no image library is needed. Media Session advertises that one size, rather than the doc's "several sizes".

**D46. Merging duplicate tracks is deferred.** Two different uploads of the same song (say, a music video and the Topic upload) can become two track rows. Add `merge_tracks` only once this actually shows up in use. Until then, "Fix metadata → choose a different source" (M2.3) is the manual fix.

**D47. No Last.fm** (removed 2026-10-12 at the user's request). The doc uses Last.fm for genre tags, similar artists and scrobbling. In this plan:
- Scrobbling is out of scope.
- Similar artists come from YouTube Music's related artists ("Fans might also like" from `get_artist`), which M3.1 already fetches.
- Genre and mood browsing uses YouTube Music's "Moods & genres" categories (`get_mood_categories` and `get_mood_playlists`). This replaces the doc's Last.fm genre tags and its BPM/energy moods.
- As a result, the doc's `tracks.genres` and `tracks.bpm` columns (doc §6) are left out, because nothing would fill them.
- `LASTFM_API_KEY` and `LASTFM_API_SECRET` in `.env` are unused.

**D48. Backend first, frontend later (decided 2026-10-12, by the user).** The server is built and verified completely before any browser UI beyond the M1.10 test page.
- **Contract for the frontend:** FastAPI's generated OpenAPI (`/docs`, `/openapi.json`), §7 (HTTP), §8 (WebSocket) and §10.3 (`PlaybackState`). Where §7 and the running API differ, fix §7.
- **Acceptance without a UI:** every `-B` milestone is proven by pytest plus, where it touches YouTube, a live run with `curl` or a script. The old "run it by hand in the browser" steps move to the frontend track.
- **Test console (added 2026-10-12):** `server/app/static/console/` is a plain HTML/JS page served by the API at `/console/` for trying every endpoint by hand. It is a tool, not the frontend. - **`web/` for now:** keep the M1.10 page as a dev harness and reference client (fetch with the profile header, the `/ws` socket, `<audio>` against `/api/stream`). Do not add libraries to it, and don't build M1.11 onward. The compose `web` service stays the placeholder nginx page.
- **Server-side facts the client needs must exist now.** The API already carries `gain_db`, `cache_state`, `art_url`, `liked`, `PlaybackState` relay and the WebSocket events, so the client never needs a backend change to start.
- **Deferred client items** (`-F`) are listed in §11 under "Track split". Nothing in them is dropped.

**D49. Lyrics are skipped (decided 2026-10-12, by the user).** M2.2 (both `-B` and `-F`), the `lyrics` table, the LRCLIB client, the lyrics endpoint, the karaoke view and the `Y` shortcut are dropped. `has_lyrics` on `GET /api/tracks/{id}` stays `null`. Migration numbers shift down by one: the next migration written is `002`. Revisit only if the user asks.

**D50. Radio is skipped (decided 2026-10-12, by the user; its autoplay part is reversed by D51).** M3.2 (`GET /api/radio/{track_id}`, the radio context, artist radio and autoplay-when-the-queue-ends) is dropped. Consequences: the home feed has no "Made for you" row from radio seeds (Daily Mixes in Phase 5 still can fill it), the artist payload has no `radio_seed_track_id`, and the artist page has no Radio button. The `radio:<track_id>` history context stays accepted but nothing creates it. A first version was built and fully removed.

**D51. Autoplay returns, powered by web recommendations (decided 2026-10-12, by the user).** This reverses the *autoplay* part of D50 only. YouTube Music radio, `GET /api/radio/{track_id}`, artist radio and "Made for you" radio seeds stay dropped.
- **Behaviour (client + server):** (1) when the user picks a song by hand, the client sends `playContext({type: "radio", id: <seed_track_id>, name: "Based on <title>"}, [seed], 0)`, so the seed plays at once and the old "Next from context" list is discarded; `user_queue` is kept. (2) When the track that starts playing is the **last item** of a `radio` context, the list is extended in the background with about 10 songs. Because a new radio context holds only the seed, this also does rule (1)'s regeneration. (3) For a playlist, album, artist or Liked Songs context nothing regenerates; when its last track starts playing and `prefs.autoplay` is on (default true), a radio seeded by that track is prepared, and when the context ends with repeat off the client starts it as `radio:<last_track_id>` with only the new tracks. **Not a "user change":** auto-advance, Next, Previous, `jumpTo` inside the current radio context, playing `user_queue` items, seek, pause/resume, repeat-one, a device transfer. `prefs.autoplay = false` stops (2) and (3); (1) still just plays the song. Plays from a generated list use the history context `radio:<seed_track_id>` (D20).
- **Server trigger** (`app/ws.py`, `maybe_autoplay`, on every stored `queue_update` and `player_state`): fire when `state.playing` is true, `current.from == "context"`, `context_pos == len(order) - 1`, the context type is `radio` or `playlist`/`album`/`artist`/`liked`, `prefs.autoplay` is not false, and both API keys are configured (otherwise nothing happens). `search` and `queue` contexts never fire. It enqueues arq job `autoplay_extend` with `_job_id = ap:<profile>:<type>:<id>:<len(context_tracks)>`, so a longer list fires again. **Addition:** a Redis gate `apf:<job id>` (10 min, `SET NX`) is taken first, because arq's job-id dedupe only holds while the job is queued or running (the worker uses `keep_result=0`): without the gate every 15-second heartbeat on the same state would re-run the job once it finished, or repeat a failure and spend credits again.
- **Server job** (`app/autoplay.py`, registered in `worker.py` with `max_tries=1`): the **examples** are the current track (title + primary artist) for the first batch of a radio; **from the second batch on they are 3 random songs of the previous batch**, all put into the **same single query and the same single model prompt**, so a batch still costs exactly one Tavily call and one model call (changed 2026-10-12 at the user's request, to give the search more than one example). The job remembers each batch it publishes (`ap:batch:<profile>:radio:<id>`: the batch's track ids and the list length the client will have once it applies the batch); the next run uses that record only if the client's list length (`after_len`) equals the recorded one, otherwise (a client that dropped the batch, or a different radio) it falls back to the song that is playing; a batch with fewer than 3 songs gives all of them. A playlist/album/artist/liked batch is recorded under the `radio:<seed>` the client will start from it (§10.3). Titles are cached in Redis for 7 days under `ap:titles:<id>` (one example) or `ap:titles:<id>+<id>+<id>` (sorted ids; never an empty list); on a miss Tavily `POST /search` (`"<title> <artist>, <title> <artist>, <title> <artist> similar songs"`, `search_depth advanced`, 10 results, raw text, 25 s) then an NVIDIA chat-completions call (`nvidia/nemotron-3-super-120b-a12b`, configurable, `temperature 0`, `max_tokens 300`, thinking off, 30 s) asks for a JSON array of up to 10 titles (the prompt names every example song and says not to include any of them); parsing takes the first `[...]` and falls back to quoted strings for a reply cut off at the token limit; titles containing any example's title, or the playing song's, are dropped. Each title is resolved with `youtube.search` (top result, 4 at a time); results already in the context or the profile's last 50 played, repeats, and anything over 10 minutes are dropped; survivors are saved with `ensure_track` in the model's order; the event `autoplay_tracks` is published to that profile only (§8). Every failure (a missing key, Tavily or the model failing, a timeout) logs, publishes the same event with `tracks: []`, and does not raise, so arq never retries.
- **Config:** `TAVILY_API_KEY`, `NVIDIA_API_KEY`, `NVIDIA_MODEL` (§3). No new dependency: `httpx`, `ytmusicapi` and arq only.
- **Client rules** are in §10.3 (`extendContext`, stale-event check, the 20 s wait). No endpoint was added; autoplay is entirely WebSocket plus the worker.

---

## 3. Configuration

| Variable | `.env` (host dev) | Container override in compose | Used by | Notes |
|---|---|---|---|---|
| `POSTGRES_USER` / `_PASSWORD` / `_DB` / `_PORT` | as set | — | db | Already in `.env` |
| `DATABASE_URL` | `postgresql://…@localhost:5432/resonance` | `postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}` | api, worker | |
| `REDIS_PORT` | 6379 | — | redis | |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://redis:6379/0` | api, worker | |
| `AUDIO_DIR` | `./audio` | `/audio` | api, worker | DB paths are relative to this (D16) |
| `PREF_CODEC` | `m4a` | — | worker | Only `m4a` or `mp3` |
| `STORAGE_LIMIT_GB` | 50 | — | worker (eviction), api (Settings) | |
| `SEARCH_HIT_THRESHOLD` | 0.82 | — | api | Calibrated in M1.4 |
| `SEARCH_CACHE_TTL` | 86400 | — | api | `ys:` keys |
| `STREAM_URL_TTL` | 3600 | — | api | `su:` keys |
| `DOWNLOAD_LOCK_TTL` | 600 | — | api, worker | `dl:lock:` keys |
| `WORKER_MAX_JOBS` | 2 | — | worker | arq `max_jobs` (D7) |
| `DOWNLOAD_RETRIES` | 3 | — | worker | 4 attempts in total (D28) |
| `YTDLP_COOKIES_FILE` | `./cookies/cookies.txt` | `/cookies/cookies.txt` | api, worker | Used only if the file exists (D8) |
| `S3_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` / `_BUCKET` | empty | — | Phase 5 | Ignored until M5.9 |
| `LASTFM_API_KEY` / `_SECRET` | set | — | nothing | **Unused** (D47). Safe to delete from `.env`. |
| `TAVILY_API_KEY` | set in `.env` (secret) | — | worker (autoplay) | Web search for similar songs (D51). Empty = autoplay does nothing. |
| `NVIDIA_API_KEY` | set in `.env` (secret) | — | worker (autoplay) | LLM that reads the search results (D51). Empty = autoplay does nothing. |
| `NVIDIA_MODEL` | `nvidia/nemotron-3-super-120b-a12b` (default; not in `.env`) | — | worker (autoplay) | Any OpenAI-compatible chat model on `integrate.api.nvidia.com`. |

D51 adds the three autoplay variables above; the rest of the plan adds none. The keys reach the api and worker containers through `env_file: .env` and must never be committed (`.env` is gitignored). Fixed values (the −14 LUFS target, the 30 s backlog interval, the 250 ms debounce) are constants in code.

---

## 4. Repository layout (target)

```
Resonance/
├── .env  .gitignore  docker-compose.yml        # compose: db, redis (exist) + api, worker, web
├── planning.md  structure.md  tracking.md  Resonance_Design_Document.pdf
├── db/init/01-extensions.sql                   # exists; runs on first volume init only
├── audio/                                      # AUDIO_DIR (gitignored)
├── cookies/                                    # cookies.txt (gitignored)
├── server/
│   ├── Dockerfile  pyproject.toml  uv.lock
│   ├── migrations/001_core.sql …               # D4
│   ├── app/
│   │   ├── main.py      # FastAPI app, lifespan (pg pool, redis, arq pool, pub/sub task), routers
│   │   ├── worker.py    # arq Settings + jobs (download_track, feed_backlog, evict, …)
│   │   ├── config.py    # the only env reader (D5)
│   │   ├── db.py        # pool + migration runner
│   │   ├── text.py      # normalize, normalize_query, parse_title
│   │   ├── youtube.py   # search (ytmusicapi + yt-dlp), rank_candidates, resolve_audio_url, download
│   │   ├── tracks.py    # ensure_track, refresh_track_search, serializers, cache_state, request_download
│   │   ├── storage.py   # path helpers, artwork processing, loudness, eviction
│   │   ├── ws.py        # WebSocket hub + Redis pub/sub
│   │   └── routes/      # profiles.py search.py stream.py tracks.py library.py (+ later phases)
│   └── tests/           # test_text.py test_rank.py test_search_sql.py (+ later)
└── web/
    ├── Dockerfile  nginx.conf  package.json  vite.config.ts  index.html
    └── src/             # see §10
```

Split a module only when it grows past about 400 lines. Don't create a file until a milestone needs it.

---

## 5. Data model

All tables use the conventions in D11. FK means foreign key. "Cascade" means `ON DELETE CASCADE`.

### 5.1 Migration `001_core.sql` (M1.1)

**profiles**

| Column | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| name | text not null unique | |
| avatar | text not null default `''` | Emoji or colour token |
| prefs | jsonb not null default `'{}'` | Keys from D37 |
| created_at | timestamptz not null default now() | |

**artists**

| Column | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| name | text not null | |
| name_norm | text not null | `normalize(name)` |
| ytm_channel_id | text unique, null | Canonical key when known (doc §9.1) |
| image_url | text null | Remote URL. Local copy goes in `art/artist/` (Phase 3). |
| bio | text null | |
| created_at | timestamptz | |

Indexes: unique `(name_norm)` **where `ytm_channel_id` is null**; GIN `(name_norm gin_trgm_ops)`.

**albums**

| Column | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| title | text not null | |
| title_norm | text not null | |
| artist_id | bigint FK artists, null | |
| year | int null | |
| cover_path | text null | Relative path (D16) |
| ytm_browse_id | text unique, null | |
| created_at | timestamptz | |

Indexes: unique `(artist_id, title_norm)` where `ytm_browse_id` is null; GIN trigram on `title_norm`.

**tracks**

| Column | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| title / title_norm | text not null | |
| primary_artist_id | bigint not null FK artists | |
| album_id | bigint FK albums, null | |
| duration_ms | int null | |
| artwork_path | text null | Relative path. Falls back to the album cover. |
| artist_names_norm | text not null default `''` | D23. All credited artists, space-joined, normalized. |
| album_title_norm | text not null default `''` | D23 |
| artist_title_norm | text not null default `''` | D23. `artist_names_norm + ' ' + title_norm` |
| search_vec | tsvector, **generated stored** | `setweight(to_tsvector('simple', title_norm), 'A') ‖ setweight(…artist_names_norm, 'B') ‖ setweight(…album_title_norm, 'C')` |
| metadata_status | text not null default `'provisional'` | Check in (`provisional`, `enriched`, `manual`). Enrichment never overwrites `manual`. |
| created_at / updated_at | timestamptz | |

Indexes: GIN `(search_vec)`; GIN trigram on `title_norm`; GIN trigram on `artist_title_norm`.

**track_artists**: `track_id` (FK, cascade), `artist_id` (FK), `role` (text, check in `primary`, `featured`, `producer`), `position` (smallint). The primary key is `(track_id, artist_id, role)`.

**sources**

| Column | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| track_id | bigint not null FK tracks, cascade | Indexed |
| video_id | text not null **unique** | D12 format |
| channel | text null | |
| title_raw | text null | Original YouTube title, kept for the fix dialog and debugging |
| duration_ms | int null | |
| quality_score | real not null default 0 | `rank_candidates` score |
| created_at | timestamptz | |

**files**: one file per source.

| Column | Type | Notes |
|---|---|---|
| source_id | bigint PK, FK sources, cascade | |
| path | text not null | Relative path (D16) |
| codec | text not null | `m4a` or `mp3` |
| bitrate | int null | kbps |
| bytes | bigint not null | |
| loudness_lufs / gain_db | real null | D32 |
| last_played | timestamptz null | D29 |
| created_at | timestamptz not null default now() | |

There's no `pinned` column (D17). Index: `(coalesce(last_played, created_at))`.

**playlists**: `id`; `owner_id` (FK profiles); `name` (not null); `name_norm`; `description` (not null, default `''`); `cover_path` (null means the client draws a collage); `is_collab` (bool, default false); `pinned_to_sidebar` (bool, default false; this is a sidebar shortcut, unrelated to file pinning in D17); `folder_id` (bigint null; its foreign key is added in 005); `created_at`; `updated_at`. Index: GIN trigram on `name_norm`.

**playlist_tracks**: `id` (PK, D18); `playlist_id` (FK, cascade); `track_id` (FK); `position` (numeric, not null); `added_by` (FK profiles); `added_at`. Index: `(playlist_id, position)`.

**likes**: `profile_id` (FK, cascade); `track_id` (FK, cascade); `created_at`. The primary key is `(profile_id, track_id)`. Also index `(track_id)`, which the pin check uses.

**play_history**: `id`; `profile_id`; `track_id`; `played_at`; `ms_played` (int); `skipped` (bool, an addition; the doc's API reports it); `context` (text null, D20); `device_id` (text null). Indexes: `(profile_id, played_at desc)` and `(track_id)`.

**user_track_stats**: `profile_id`; `track_id`; `play_count` (int, default 0); `skip_count` (int, default 0); `last_played`. The primary key is `(profile_id, track_id)`. It's maintained by the history endpoint (D38), not a materialized view.

**download_jobs**: `id`; `video_id` (text **unique**); `status` (check in `queued`, `running`, `done`, `failed`); `priority` (check in `high`, `low`); `attempts` (int, default 0); `error` (text null); `created_at`; `updated_at`. Index: `(status, priority, created_at)`.

**devices**: `id` (text PK, D21); `profile_id` (FK); `name`; `last_seen`; `state` (jsonb, default `'{}'`; this device's last `player_state`).

**playback_state** (an addition, D34): `profile_id` (PK, FK); `state` (jsonb, not null; `PlaybackState`); `active_device_id` (text null); `updated_at`.

### 5.2 Later migrations (added in their milestones, not before)

**Numbering (changed 2026-10-12):** an applied migration is never edited, and the milestones below land at different times, so numbers are assigned **in the order migrations are written** (`002`, `003`, …). The names and contents below stay; only the numbers shift if the order differs.

| Migration | Milestone | Contents |
|---|---|---|
| `002_polish.sql` | ~~M2.2~~, M2.5 | ~~**lyrics**~~ (skipped, D49): `track_id` (PK, FK, cascade), `synced_lrc`, `plain_text`, `source` (`lrclib` or `manual`), `fetched_at` (not null). A row with both lyric fields null means "not found"; look again after 30 days. **recent_searches**: `profile_id`, `query_norm`, `query`, `searched_at`, with primary key `(profile_id, query_norm)` (changed 2026-10-12: keyed on the normalized text so "Tum Hi Ho" and "tum hi ho" are one entry; migration `002_recent_searches.sql`). |
| `003_discovery.sql` | M3.1, M3.4 | **saved_albums** and **follows**: `(profile_id, album_id \| artist_id, created_at)`, with the primary key on the pair. **imports**: `id`, `profile_id`, `kind` (`youtube` or `csv`), `source_ref`, `playlist_id`, `status` (`running`, `done`, `failed`), `total`, `matched`, `unmatched` (jsonb, default `'[]'`), `created_at`, `finished_at`. |
| `004_devices.sql` | M4.2 | **device_downloads**: `device_id` (FK devices, cascade), `playlist_id` (FK, cascade), `created_at`, with primary key `(device_id, playlist_id)`. |
| `005_extras.sql` | Phase 5 | **playlist_folders**: `id`, `owner_id`, `name`, `parent_id` (nullable, self-referencing), plus the FK on `playlists.folder_id`. **playlist_members**: `(playlist_id, profile_id)`. **mixes**: `id`, `profile_id`, `kind` (`daily` or `discover`), `name`, `track_ids` (bigint[]), `generated_at`. **new_releases**: `album_id` (PK), `artist_id`, `detected_at`. |

### 5.3 Storage estimates

These come from doc §6. At AAC 128 kbps, 1,000 songs take about 3.5 GB, so the default 50 GB budget holds roughly 14,000 songs before eviction starts.

---

## 6. Redis and background jobs

### 6.1 Redis keys

| Key | Value | TTL | Written by |
|---|---|---|---|
| `ys:<type>:<nq>` | JSON list of ranked `RemoteTrack`s | `SEARCH_CACHE_TTL` | api (search) |
| `su:<video_id>` | JSON `{url, http_headers, mime}` | `STREAM_URL_TTL` | api (stream) |
| `dl:lock:<video_id>` | `"1"` | `DOWNLOAD_LOCK_TTL` | `request_download`. Deleted by the worker on success or final failure. |
| `arq:queue`, `arq:job:dl:<video_id>`, … | managed by arq | — | arq |
| `ytm:artist:<id>`, `ytm:album:<id>` | JSON | 24 h | api (Phase 3). (`radio:<video_id>` was dropped with radio, D50.) |
| `ap:titles:<id>` or `ap:titles:<id>+<id>+<id>` | JSON list of recommended titles, keyed by the example song(s) | 7 days | worker (autoplay, D51). Never an empty list. |
| `ap:batch:<profile>:radio:<id>` | `{len, ids}`: the last published batch | 6 h | worker (autoplay, D51): lets the next batch pick 3 of these as examples. |
| `apf:<arq job id>` | `"1"` | 10 min | api (autoplay trigger gate, D51) |
| Pub/sub `ws:all`, `ws:profile:<id>` | JSON messages (§8) | — | api, worker |

### 6.2 arq functions (all in `app/worker.py`)

| Function | Trigger | Phase |
|---|---|---|
| `download_track(video_id)` | `request_download` (D27) | 1 |
| `feed_backlog()` | cron every 30 s (D27) | 1 |
| `evict()` | cron hourly, plus after each successful download (D33) | 1 |
| `run_import(import_id)` | `POST /api/import` | 3 |
| `update_ytdlp()` | admin button | 4 |
| `autoplay_extend(profile_id, context_key, after_len, seed_track_id, exclude_track_ids)` | `maybe_autoplay` in `app/ws.py` (D51) | 2 |
| `daily_mixes()` (04:00 daily), `discover_weekly()` (Sunday 22:00), `release_radar()` (weekly) | cron | 5 |

Worker settings: `max_jobs = WORKER_MAX_JOBS`, `max_tries = DOWNLOAD_RETRIES + 1`, `keep_result = 0`, `job_timeout = 300 s`.

---

## 7. HTTP API

These are FastAPI's defaults: JSON bodies, and errors returned as `{"detail": "..."}` with the right HTTP status. There's no custom error envelope. Every endpoint except artwork and health uses `current_profile` (D10). The doc's `/api/search?q=&type;=` is a typo for `type=`.

### 7.1 Shared objects

**Track** (any track the database knows about):

```json
{ "id": 812, "video_id": "dQw4w9WgXcQ", "title": "Tum Hi Ho",
  "artists": [{"id": 3, "name": "Arijit Singh"}], "album": {"id": 5, "title": "Aashiqui 2"},
  "duration_ms": 262000, "art_url": "/api/art/track/812", "cache_state": "cached",
  "gain_db": -2.4, "liked": true }
```

- `album` and `gain_db` may be null.
- `art_url` is always the art endpoint. The server falls back to the album cover, and returns 404 when there's no art at all, in which case the client shows a placeholder.

**RemoteTrack** (a YouTube result):

```json
{ "video_id": "…", "title": "…",
  "artists": [{"name": "…", "ytm_channel_id": "UC…"}],
  "album": {"title": "…", "ytm_browse_id": "MPRE…"},
  "duration_ms": 262000, "thumb_url": "https://…", "in_library": false, "track_id": null }
```

`album` and `ytm_channel_id` may be null.

**TrackHint** (input to `ensure`): the RemoteTrack fields without `in_library` and `track_id`.

**ArtistSummary** `{id, name, art_url}`. **AlbumSummary** `{id, title, artist: {id, name}, year, art_url}`. **PlaylistSummary** `{id, name, owner_id, track_count, cover_url | null, collage: [art_url ×≤4]}`.

### 7.2 Endpoints

| Phase | Method and path | Request | Response and notes |
|---|---|---|---|
| 1 | `GET /api/health` | — | `{ok, yt_dlp_version}`. No profile needed. Used by the compose healthcheck. |
| 1 | `GET /api/profiles` | — | `[{id, name, avatar}]`. No profile needed (it powers the picker). |
| 1 | `POST /api/profiles` | `{name, avatar?}` | Profile. No profile needed. |
| 1 | `GET /api/me` | — | `{id, name, avatar, prefs}` |
| 1 | `PATCH /api/me` | `{name?, avatar?, prefs?}` | `prefs` is shallow-merged |
| 1 | `GET /api/search` | `q`, `type=all\|songs\|artists\|albums\|playlists` (default `all`), `remote=auto\|always\|never` (D26) | `{query_norm, cache_hit, top: {kind, item} \| null, songs: Track[], artists: ArtistSummary[], albums: AlbumSummary[], playlists: PlaylistSummary[], youtube: RemoteTrack[]}`. `cache_hit` uses the songs score only. `youtube` leaves out any `video_id` already in `songs`. YouTube artist and album tabs come in Phase 3 (M3.1). |
| 1 | `GET /api/suggest` | `q` | ≤ 8 `{kind: track\|artist\|album, id, label, sublabel}`. Local only, under 30 ms, minimum 2 characters. |
| 1 | `GET /api/suggest/queries` | `q` | ≤ 6 strings: YouTube Music's search autocomplete, cached in Redis as `yq:<query_norm>` for `SEARCH_CACHE_TTL`; `[]` (not cached) when YouTube Music fails or takes over 2 s. Separate from `/api/suggest` so local suggestions stay instant (added 2026-10-13). |
| 1 | `GET /api/stream/{video_id}` | Range header, `download=1?` | Audio bytes (D29 and D30). Works for any valid `video_id`. `download=1` adds `Content-Disposition: attachment` with an "Artist - Title" filename, so the browser saves the file instead of streaming it; 409 if the file isn't cached yet (added 2026-10-13, for save-to-device). |
| 1 | `POST /api/tracks/ensure` | `TrackHint[]` (≤ 200) | `Track[]` in the same order (D13) |
| 1 | `GET /api/tracks/{id:int}` | — | Track + `sources: [{video_id, channel, quality_score, cached}]` + `has_lyrics` (null until Phase 2) |
| 1 | `POST /api/tracks/{video_id}/cache` | `priority=high\|low` | `{cache_state}`. The path parameter is a **video_id**, as in the doc. |
| 1 | `GET /api/art/{kind}/{id}` | kind: `track`, `album`, `artist`, `playlist` | JPEG with long-lived cache headers, or 404. No profile needed. |
| 1 | `GET /api/playlists` | — | `PlaylistSummary[]` (owned; collaborative ones too from Phase 5) |
| 1 | `POST /api/playlists` | `{name, description?}` | Playlist |
| 1 | `GET /api/playlists/{id}` | — | `{…summary, description, items: [{item_id, position, added_at, track: Track}]}`. Pre-caches the first five uncached tracks at low priority (D19). |
| 1 | `PATCH /api/playlists/{id}` | `{name?, description?, pinned_to_sidebar?}` | Playlist |
| 1 | `DELETE /api/playlists/{id}` | — | 204. Tracks and files are kept. |
| 1 | `PUT /api/playlists/{id}/cover` | multipart image, ≤ 5 MB | Re-encoded to 600×600 JPEG with ffmpeg (D45) |
| 1 | `DELETE /api/playlists/{id}/cover` | — | Back to the collage |
| 1 | `POST /api/playlists/{id}/tracks` | `{track_ids: [], at: "end"\|"start"}` | Items. Requests low-priority downloads (D19). |
| 1 | `DELETE /api/playlists/{id}/tracks` | `{item_ids: []}` | 204 |
| 1 | `PATCH /api/playlists/{id}/tracks` | `{item_id, before_item_id \| null}` | Move. `null` means move to the end (D18). |
| 1 | `PUT` / `DELETE /api/me/likes/{track_id}` | — | 204. Liking requests a low-priority download. |
| 1 | `GET /api/me/likes` | `offset`, `limit` | `Track[]`, newest first (the Liked Songs page) |
| 1 | `GET /api/me/library` | — | `{liked_count, playlists: PlaylistSummary[]}` (+ `albums`, `artists` in Phase 3) |
| 1 | `GET /api/me/recent` | `limit` (default 12) | `[{context, name, art_url, last_played}]`: distinct contexts from history (D20) |
| 1 | `POST /api/me/history` | `[{track_id, played_at, ms_played, skipped, context, device_id}]` | 204 (D38) |
| 1 | `GET /api/settings` | — | `{codec, storage_limit_bytes, storage_used_bytes, file_count}`, read-only (D37) |
| 1 | `WS /ws` | `?profile=&device=&name=` | §8 |
| ~~2~~ | ~~`GET /api/tracks/{id}/lyrics`~~ | — | **Skipped (D49).** |
| 2 | `PATCH /api/tracks/{id}` | `{title?, artists?: [names], album?, year?}` | Sets `metadata_status = 'manual'`. Rewrites the tags. |
| 2 | `GET /api/tracks/{id}/candidates` | — | Ranked YouTube alternatives for the track (`rank_candidates`, each a RemoteTrack), for the fix-metadata choice list (M2.3) |
| 2 | `POST /api/tracks/{id}/sources` | `{video_id}` | Adds the source and starts a high-priority download. When done it becomes the preferred source and the old file is deleted. |
| 2 | `GET` / `POST` / `DELETE /api/me/searches` | `POST {query}` | Recent searches, newest 20 |
| 3 | `GET /api/home` | — | `{sections: [{id, title, items}]}` (M3.3) |
| ~~3~~ | ~~`GET /api/radio/{track_id}`~~ | | **Skipped (D50).** |
| 3 | `GET /api/artists/{id}` / `GET /api/albums/{id}` | id or `ytm:<…>` (D42) | Artist or album page |
| 3 | `PUT` / `DELETE /api/me/follows/{artist_id}`, `/api/me/albums/{album_id}` | — | 204 |
| 3 | `POST /api/import` | JSON `{url, name?}` or a multipart CSV (≤ 2 MB) | `{import_id}`. Progress arrives over WebSocket. |
| 3 | `GET /api/imports/{id}` | — | `{status, total, matched, unmatched, playlist_id}` |
| 4 | `GET /api/devices` | — | `[{id, name, online, active, last_seen}]` (built in M4.1-B; the `devices` WebSocket message carries `last_seen` too) |
| 4 | `PUT` / `DELETE /api/me/downloads/{playlist_id}` | `?device=` | Marks or unmarks a playlist for offline use, which pins its files (D17) |
| 4 | `GET /api/admin/status` | — | Job counts, failed jobs, disk usage, yt-dlp version, over-budget warning |
| 4 | `POST /api/admin/jobs/{video_id}/retry` | — | Re-request at high priority |
| 4 | `POST /api/admin/jobs/{video_id}/alternative` | — | Next-best source from a search for the track, using its existing metadata |
| 4 | `POST /api/admin/ytdlp/update` | — | M4.4 |
| 5 | `GET /api/me/stats` | `range=4w\|6m\|1y\|all` | Built (M5.5-B): `{range, since, plays, skips, minutes_listened, days_listened, top_tracks, top_artists}`. Counted plays follow D38; minutes are real listening time. |
| 5 | `GET /api/me/wrapped` | `year=` | Built (M5.5-B): `{year, plays, minutes_listened, cards[]}`; card ids `minutes, top_song, top_songs, top_artist, top_artists, top_album?, busiest_day, discoveries`. |
| 5 | `GET /api/browse/moods`, `GET /api/browse/moods/{params}` | — | YouTube Music "Moods & genres" categories, and the playlists in one category (M5.1) |
| 5 | `/api/folders` (CRUD), `POST` / `DELETE /api/playlists/{id}/members` | — | Folders and collaborators |

---

## 8. WebSocket protocol

**Connect:** `GET /ws?profile=<id>&device=<uuid>&name=<urlencoded>`. On connect, the server:
1. upserts the `devices` row,
2. registers the socket,
3. sends `state` to the new socket,
4. broadcasts `devices` to the profile.

The client sends `ping` every 25 s and reconnects with exponential backoff (1 s → 30 s cap). After reconnecting it refetches open TanStack queries, because events may have been missed.

### Client → server

| type | Fields | Server action |
|---|---|---|
| `player_state` | `track_id, position_ms, playing, at` (client epoch ms) | Sent on play, pause, seek and track change, and every 15 s while playing. Stored in `devices.state`. If it comes from the active device, also stored in `playback_state` and relayed. If `playing = true` and the sender isn't active, the sender becomes active (D35). |
| `queue_update` | `state: PlaybackState` | Stored in `playback_state.state`, then relayed |
| `transfer` (M4.1-B, built) | `to_device` | The target must be one of this profile's **online** devices, else the sender gets `error`. Sets the active device and broadcasts `active_device` with `resume: true` and the stored `state` (queue + position). The sender should send a `player_state` just before, so the position is fresh. |
| `ping` | — | `pong` |

### Server → client

| type | Fields | Channel |
|---|---|---|
| `state` | `state, active_device_id, devices` | Direct, on connect |
| `track_cached` | `video_id, track_id` | `ws:all` |
| `download_progress` (an addition) | `video_id, percent` (≤ 2 per second per video) | `ws:all` |
| `download_failed` (an addition) | `video_id, error` | `ws:all` |
| `devices` | `list: [{id, name, online, active}]` | profile |
| `player_state` | as above, plus `device` | profile, excluding the sender |
| `queue_update` | `state` | profile, excluding the sender |
| `error` (addition) | `detail` | direct. Sent for a refused `transfer`. |
| `active_device` | `device_id, resume, state?` | profile. `state` (the stored `PlaybackState`) is included when `resume` is true. The target loads the state and plays only if `resume` is true. Every other device pauses. |
| `import_progress` (Phase 3) | `import_id, done, total` | profile |
| `autoplay_tracks` (D51) | `context: {type, id}, after_len, seed_track_id, tracks: Track[], stats: {titles, resolved, kept, examples: [track ids used as examples]}` | profile only. Published when `autoplay_extend` finishes, **also with `tracks: []`** after any failure so the client can stop waiting. `after_len` is `len(context_tracks)` when the job was triggered; `stats` is informational (how many titles the model gave, how many resolved to a song, how many were kept). |
| `pong` | — | direct |

---

## 9. Core algorithm specs (Phase 1)

### 9.1 Normalization (`app/text.py`)

`normalize(s)`, applied in this order:
1. NFKC, then `casefold()`.
2. NFD, then drop combining marks **only in U+0300–U+036F**. This removes Latin accents but keeps Devanagari and other script vowel signs. Then recompose to NFC.
3. Delete apostrophes (`'` and `’`), so "don't" becomes "dont". Replace `&` with ` and `.
4. Replace every character that is not a letter, a number or a combining mark with a space.
5. Collapse whitespace and trim.

`normalize_query(q)`: run `normalize(q)`, then remove whole-word noise phrases, longest first. The phrases are: `official music video`, `official video`, `official audio`, `lyric video`, `lyrical video`, `video song`, `full song`, `full video`, `lyrics`, `lyric`, `audio`, `hd`, `hq`, `4k`. **If this leaves an empty string, return `normalize(q)` instead.**

Required test cases:

| Input | Output |
|---|---|
| `Beyoncé` | `beyonce` |
| `Tum Hi Ho (Official Video) HD`, via `normalize_query` | `tum hi ho` |
| `Simon & Garfunkel` | `simon and garfunkel` |
| `तुम ही हो` | unchanged |
| `audio`, via `normalize_query` | `audio` |

### 9.2 Title parsing, Phase 1 stages (doc §9.1 stages 1–3) (`parse_title`)

**Input:** a yt-dlp info dict, or a TrackHint. **Output:** `{title, artists: [names], featured: [names], album, year}`.

1. **From the hint:** if a TrackHint from ytmusicapi has artists, use its fields directly.
2. **From yt-dlp fields:** if `track` and (`artists` or `artist`) are present, use them. A string `artist` is split on `, `. Also use `album` and `release_year`.
3. **From a Topic description:** if the description contains `Provided to YouTube by`, the first non-empty line after it is `Title · Artist1 · Artist2`, the next non-empty line is the album, and `Released on: YYYY-…` gives the year.
4. **Rule-based fallback:**
   - Remove bracketed segments (`(…)` or `[…]`) that contain noise words (official, video, audio, lyric, lyrics, hd, hq, 4k, visualizer, full song).
   - Split on ` - `, ` – `, ` — ` and ` | `. Segment 1 is the artist, segment 2 the title, and the next non-noise ` | ` segment the album.
   - Pull `feat.`, `ft.` and `featuring` credits out of the title or artist into `featured`. Split artist lists only on `,`, so names like "Simon & Garfunkel" stay whole.
   - With no separator, the title is the cleaned title and the artist is the channel name with ` - Topic`, `VEVO` and `Official` removed.

Required test (from the doc): `Arijit Singh - Tum Hi Ho (Official Video) | Aashiqui 2 | HD` → title `Tum Hi Ho`, artist `Arijit Singh`, album `Aashiqui 2`. Add 10–15 more real titles, including feat. credits, Topic descriptions and titles with no separator.

### 9.3 Local search scoring (D24)

`nq = normalize_query(q)`. Candidates:

```
search_vec @@ plainto_tsquery('simple', nq)  OR  title_norm % nq  OR  nq <% artist_title_norm
```

| Component | Range | Definition |
|---|---|---|
| `fts` | 0 or 1 | 1 if `search_vec @@ plainto_tsquery('simple', nq)`, meaning every query word appears in the title, artists or album |
| `trgm` | 0–1 | `greatest(similarity(title_norm, nq), similarity(artist_title_norm, nq), word_similarity(nq, artist_title_norm \|\| ' ' \|\| album_title_norm))` (changed 2026-10-12: the album was added to the word_similarity haystack so "kesariya brahmastra" scores as a hit) |
| `personal` | 0–1 | `least(1, coalesce(play_count, 0) / 20.0 + (liked ? 0.5 : 0))` |
| **score** | 0–1 | `0.55·fts + 0.35·trgm + 0.10·personal`. Order by score, descending; limit 30. |

- **Hit** when `songs[0].score ≥ SEARCH_HIT_THRESHOLD`.
- **Top result:** an artist whose `name_norm = nq` exactly; otherwise `songs[0]`.
- **Other tabs:** artists, albums and playlists use trigram `%` or a prefix `LIKE nq || '%'` on their `*_norm` column, ordered by similarity, limit 20. Playlists are limited to the current profile's own.

Sanity checks:
- An exact title query gives fts 1 and trgm 1, so 0.90: a hit.
- A typo query ("tum hee ho") gives fts 0, about 0.2: a miss. The local song is still listed first, YouTube results appear below it, and duplicates are removed by `in_library`. This matches doc §4.1's "find the same song".

**Calibration (M1.4):** `server/tests/search_fixture.json` holds about 40 tracks and about 30 queries, each marked expected `hit`, `miss` or `rank-1 track`. Cover English, Hindi and other regional romanized titles, artist + title, title + artist, partial artist, typos, and artist-only queries. `test_search_sql.py` asserts the expectations. Tune the weights or threshold until it passes, and record the final values in D24.

### 9.4 YouTube candidate ranking (`rank_candidates`, doc §4.4)

| Signal | Score |
|---|---|
| Result came from the ytmusicapi `songs` filter, **or** channel ends in ` - Topic`, **or** channel name normalized equals the parsed artist normalized, **or** channel ends in `VEVO` | +3.0 |
| Duration within ±10 s of the top ytmusicapi song entry, when both lists exist | +1.5 |
| Title contains `live`, `cover`, `remix`, `slowed`, `reverb`, `8d` or `karaoke` as whole words, and the query doesn't | −2.0 (applied once) |
| Duration over 12 minutes | −5.0 |
| `0.1 × log10(view_count + 1)`; 0 when views are missing | tie-breaker |

Sort is stable and descending, so ties keep YouTube's own order. The score is stored in `sources.quality_score` when a source is created. Test: a fixed candidate list, including a cover, a live version, a one-hour loop and a Topic upload, must come out in the expected order.

### 9.5 Download job (`download_track`)

1. If the preferred source for `video_id` has a file, mark the job `done`, delete the lock and return (idempotent).
2. Set `download_jobs` to `running` and `attempts += 1`.
3. Run yt-dlp in a thread. Options:
   - format `bestaudio[ext=m4a]/bestaudio`
   - `outtmpl` set to the D16 path without the extension
   - a `FFmpegExtractAudio(preferredcodec = PREF_CODEC)` postprocessor
   - `writethumbnail`
   - `retries: 5`, `fragment_retries: 5`, `concurrent_fragment_downloads: 4`
   - the cookies file if it exists
   - a progress hook that sends `download_progress` from the thread to the event loop (`run_coroutine_threadsafe`), throttled to 2 per second
4. Run `parse_title(info)`. In Phase 2 this becomes `enrich(info)`.
5. Upsert the artists, album, track and source rows (using the existing provisional track if there is one) and call `refresh_track_search`. Never overwrite `manual` metadata.
6. Artwork: center-crop the thumbnail to 600×600 with ffmpeg and save it to `art/track/<video_id>.jpg`. Delete the original thumbnail.
7. Measure loudness (D32), then insert the `files` row with byte size, codec and bitrate (from ffprobe or the info dict).
8. Write clean tags and the cover with mutagen (D31).
9. Set the job to `done`, publish `track_cached {video_id, track_id}` on `ws:all`, delete the lock, run `evict()`, and sleep a random 1–3 s.

Errors follow D28. If the process crashes partway, the next attempt starts again from step 1: a partial file with no `files` row gets overwritten.

### 9.6 Smart shuffle (client, `web/src/player/shuffle.ts`) *[FRONTEND, deferred]*

Fisher–Yates shuffle, followed by one repair pass: whenever `artist(i) == artist(i−1)`, swap `i` with the nearest later `j` whose artist is different. Tests:
- 5 + 5 tracks from two artists → no two neighbours share an artist.
- All tracks by one artist → still a valid permutation.
- The result is always a permutation of the input.

### 9.7 Queue reducer *[FRONTEND, deferred]*

See §10.3.

---

## 10. Client architecture (`web/`) *[FRONTEND, deferred (D48). §10.3 `PlaybackState` is part of the server contract and stays binding now.]*

### 10.1 Structure

```
src/
  main.tsx  App.tsx (routes + layout)  index.css (Tailwind + theme tokens)
  api.ts            # fetch wrapper (adds X-Profile-Id) + TanStack Query hooks
  ws.ts             # single socket, reconnect, dispatch to stores + query cache
  stores/           # profile.ts, player.ts (PlaybackState + audio status), downloads.ts (video_id → state/%)
  player/           # queue.ts (pure reducer), shuffle.ts, engine.ts, mediaSession.ts, shortcuts.ts
  pages/            # Profiles, Home, Search, Library, Liked, Playlist, Settings (+ Album, Artist, Admin later)
  components/       # Sidebar, BottomTabs, NowPlayingBar, QueuePanel, TrackRow, Cover, …
```

**Routes:** `/profiles`, `/` (Home), `/search`, `/library`, `/liked`, `/playlist/:id`, `/settings`. Later phases add `/album/:id`, `/artist/:id` and `/admin`. The queue, lyrics and full-screen player are **panels and overlays**, not routes.

### 10.2 Layout and theme

- **Layout:** on wide screens, three columns (sidebar, main view, optional right panel for the queue or lyrics) with the Now Playing bar at the bottom. Below 768 px, a bottom tab bar (Home, Search, Library) with a mini player above it (doc §10).
- **Theme:** colour tokens are CSS variables on `:root`, and the dark set applies under `[data-theme="dark"]`. The default follows `prefers-color-scheme`, and the choice is stored in `prefs.theme` (`system`, `light` or `dark`).
- **Download indicator:** every `TrackRow` shows the D14 states, using live percentages from `stores/downloads`.

### 10.3 `PlaybackState`

This is stored in `playback_state.state` and sent in `queue_update`:

```json
{
  "context": {"type": "playlist", "id": "12", "name": "Road trip"},
  "context_tracks": [812, 640, 77, 91],
  "order": [0, 1, 2, 3],
  "context_pos": 0,
  "user_queue": [505],
  "current": {"track_id": 812, "from": "context"},
  "shuffle": false,
  "repeat": "off",
  "position_ms": 63400,
  "playing": true
}
```

- `order` holds positions in `context_tracks`. It's the identity order when shuffle is off.
- `current.from` is `context` or `user`.
- `repeat` is `off`, `all` or `one`.

**Reducer actions:**

| Action | Behaviour |
|---|---|
| `playContext(context, tracks, start)` | Replaces the context, keeps `user_queue`, sets `context_pos = start`. If shuffle is on, the start track goes first and the rest are smart-shuffled. |
| `playNext(ids)` | Adds to the front of `user_queue` |
| `addToQueue(ids)` | Adds to the end of `user_queue` |
| `next({auto})` | `auto && repeat == 'one'` → restart the current track. Otherwise, if `user_queue` isn't empty, take its first item (`context_pos` doesn't move). Otherwise `context_pos + 1`. Past the end: `repeat == 'all'` → back to 0 (reshuffling if shuffle is on); otherwise **stop**, or start autoplay radio from Phase 3. |
| `previous(position_ms)` | If position > 3000 ms, seek to 0. Otherwise, if `context_pos > 0`, go to `context_pos − 1`. Otherwise seek to 0. |
| `jumpTo(section, index)` | Play that item. Earlier `user_queue` items are dropped; jumping within the context sets `context_pos`. |
| `remove(section, index)` and `move(section, from, to)` | Edit `user_queue`, or the upcoming part of `order` |
| `toggleShuffle()` | On: the current track goes to position 0 and the rest are smart-shuffled. Off: `order` returns to the identity and `context_pos` becomes the current track's original index. |
| `cycleRepeat()` | off → all → one → off |
| `peekNext(n)` | The next n track IDs, without changing state. Used for pre-caching. |
| `extendContext(tracks)` (D51) | Append the ids not already in `context_tracks`; append their new positions to the end of `order` (**unshuffled, even when shuffle is on**); leave `context_pos` alone. Then send `queue_update` as usual. |

Every action is unit-tested in `queue.test.ts`.

**Autoplay rules for the client (D51; the server half is built, the client half is deferred):**
- **A hand-picked song** (a search result, a track row anywhere, "Play" on a track) immediately sends `playContext({type: "radio", id: <seed_track_id>, name: "Based on <title>"}, [seed], 0)`. The seed starts at once; the old "Next from context" list is discarded; `user_queue` stays. The server sees a radio context whose only (= last) item is playing and starts the job.
- **Not a hand-picked song:** auto-advance, Next, Previous, `jumpTo` within the current radio context, playing `user_queue` items, seek, pause/resume, repeat-one, and a device transfer.
- **`autoplay_tracks` arrives:** apply it only if the current context equals `context` **and** `len(context_tracks) == after_len`; otherwise it is stale (the user changed song meanwhile) and is ignored. For a `radio` context call `extendContext`. For a playlist, album, artist or liked context keep the tracks as a pending radio and start it when that context ends, as `radio:<last_track_id>` containing only the new tracks (so the last track does not play twice).
- **The last track ends before the tracks arrive:** show "Finding more songs…" and wait up to 20 s, then stop as before. An event with `tracks: []` stops at once.
- **`prefs.autoplay` false** (default true): the client does not wait for or start anything beyond the hand-picked song. The server also does not fire.
- **History:** plays from a generated list report the context `radio:<seed_track_id>` (D20).
- D19 still applies: request high-priority caching of the next two items. A radio that was started has no download yet, so the first track is played live (§D30).

### 10.4 Player behaviour (Phase 1)

- **Before playing an uncached result**, `await POST /api/tracks/ensure`. Then set `audio.src = /api/stream/<video_id>?profile=<id>`.
- **When a track starts:**
  - update Media Session metadata and handlers (`play`, `pause`, `previoustrack`, `nexttrack`, `seekto`, `seekbackward`, `seekforward`) and `setPositionState`
  - send `player_state`
  - pre-cache `peekNext(2)` for any item that isn't `cached`
- **When a track ends, is skipped or is replaced:** queue a history entry (D38) and flush it right away, or keep it in memory while offline.
- **On load:** take the WebSocket `state`, load the current track **paused** at `position_ms`, and never autoplay.

**Keyboard shortcuts** (doc §8.5) are ignored while focus is in a text field:

| Key | Action |
|---|---|
| Space | Play / pause |
| → / ← | Seek ±5 s |
| Shift + → / ← | Next / previous track |
| ↑ / ↓ | Volume ±5 % |
| Ctrl/Cmd + K | Focus search |
| L | Like the current track |
| S | Toggle shuffle |
| R | Cycle repeat |
| Q | Toggle the queue panel |
| Y | Toggle the lyrics panel (Phase 2) |

---

## 11. Milestones

### Track split (D48, 2026-10-12)

**Backend track, in this order. Done = acceptance + tests pass + `tracking.md`/`structure.md` updated.**

| # | Milestone | Status when written |
|---|---|---|
| 1 | M0.1–M0.2, M1.1–M1.9 | done |
| 2 | M1.10 (bare page, kept as a dev harness) | done |
| 3 | **M1.13-B** history API | next |
| 4 | **M1.14-B** library API: likes, playlists, library, recent | |
| 5 | **M1.15-B** settings API | |
| 6 | **Phase 1 backend exit check** | |
| 7 | **M2.1** enrichment (done), ~~M2.2-B lyrics~~ (skipped, D49), **M2.3-B** fix-metadata API, **M2.5-B** recent searches | |
| 8 | **M3.1-B** artist/album API (done), ~~M3.2-B radio~~ (skipped, D50), **M3.3-B** home feed (done), **M3.4-B** import | |
| 9 | **M4.1-B** transfer, **M4.2-B** offline-pin API, **M4.3-B** isolation review, **M4.4-B** admin API, **M4.5** backups | |
| 10 | **M5.1-B, M5.2, M5.3, M5.4, M5.5-B, M5.6-B, M5.9** | |

**Frontend track (deferred, built later against the API):** M1.11 app shell, M1.12 search UI, M1.13-F player (queue reducer, shuffle, engine, Media Session, shortcuts), M1.14-F library pages, M1.15-F settings page, M2.2-F lyrics view, M2.3-F fix-metadata dialog, M2.4 Web Audio engine, M2.5-F (recent searches UI, PWA, colour, full-screen player), M3.1-F to M3.4-F pages and radio client, M4.1-F device picker, M4.2-F IndexedDB offline, M4.3-F profile polish, M4.4-F admin page, M5.7 Chromecast, M5.8 animated background, and the client side of the rest. Also the web image (nginx, §D6), the device matrix (§12) and all manual browser acceptance.

Each milestone lists its goal, tasks, acceptance criteria and tests. Do the milestones in order. Inside a milestone, the order of tasks is flexible.

### Phase 0: Foundations

**M0.1 Repository setup**
- Run `git init`. Add a `.gitignore` covering `.env`, `audio/`, `cookies/`, `.venv/`, `node_modules/`, `dist/`, `__pycache__/` and `spikes/`. Create the empty `server/` and `web/` folders.
- Write the first version of `structure.md`.
- **Acceptance:** `git status` is clean apart from intended files, and `.env` is ignored.

**M0.2 Tool spike.** These are throwaway scripts in `spikes/`. Delete them afterwards, and write the findings into `tracking.md`. Verify:
1. `ytmusicapi.search(..., filter="songs")` on 6 queries (English, Hindi, Punjabi, Tamil, a typo, artist-only) returns `videoId`, `title`, `artists`, `album` and `duration_seconds`, in under 1.5 s. Flat yt-dlp `ytsearch10:` works as a fallback. **This decides D25.**
2. yt-dlp, with Deno on the PATH, resolves a `bestaudio[ext=m4a]` URL. `httpx` with `Range: bytes=0-1023` and yt-dlp's `http_headers` gets back a 206. A minimal proxy plays the URL in Chrome and Safari, ideally iOS Safari too, including seeking.
3. Downloading with `FFmpegExtractAudio` to m4a remuxes rather than re-encoding (check the codec and bitrate). `loudnorm` JSON parses correctly. The ffmpeg center crop to 600×600 works. Mutagen tag and cover writing works on m4a.
4. The installed Starlette version's `FileResponse` returns 206 and `Content-Range` for Range requests (D29).
5. In arq, `_job_id` dedupe returns `None` for a duplicate while a job is queued, and with `keep_result = 0` the same ID can be enqueued again once the job finishes (D27).
6. Whether the search fixture's typical queries reach the D24 hit threshold. This is a rough check here; the real one is M1.4.

- **Acceptance:** every point is recorded as confirmed or changed, and any changed decision is updated in §2.

### Phase 1A: The core loop (backend first, then a bare UI)

**M1.1 Server skeleton and infrastructure**
- `server/pyproject.toml` (D2), `Dockerfile` (D9), `app/config.py` (D5), `app/db.py` (pool and migration runner, D4), `migrations/001_core.sql` (§5.1), the Redis client and arq pool in the lifespan, `app/worker.py` with arq `Settings` and the migration call at startup, and `GET /api/health`.
- `docker-compose.yml`: add `api`, `worker` and `web` (the `web` entry is a placeholder until M1.11), the env overrides from §3, the volume mounts `./audio:/audio` and `./cookies:/cookies:ro` (D8), `depends_on` with `condition: service_healthy`, and an api healthcheck against `/api/health`.
- **Host dev:** `docker compose up db redis`, then `uv run uvicorn app.main:app --reload` and `uv run arq app.worker.Settings`.
- **Acceptance:**
  - `docker compose up` brings every service up healthy.
  - Migrations apply exactly once even when the api and worker start together.
  - `/api/health` returns the yt-dlp version.

**M1.2 Text utilities.** `normalize`, `normalize_query` and `parse_title` (§9.1, §9.2).
- **Tests:** `test_text.py`, with every required case from §9.1 and §9.2.

**M1.3 Profiles.** The `current_profile` dependency (D10), `GET`/`POST /api/profiles`, and `GET`/`PATCH /api/me`.
- **Acceptance:** a request with no profile gets a 401. Both the header and `?profile=` work.

**M1.4 Local search and suggest.** `refresh_track_search`, the scoring SQL (§9.3), the songs, artists, albums and playlists queries, top-result logic, and `/api/suggest`. Add a seed script, `python -m app.tools.seed --n 10000`, that generates synthetic tracks for timing.
- **Tests:** `test_search_sql.py` checks the fixture expectations against a `resonance_test` database (created on demand, with migrations applied).
- **Acceptance:**
  - The fixture passes.
  - With 10k tracks, search takes under 80 ms and suggest under 30 ms, measured server-side.
  - `EXPLAIN` shows the GIN indexes in use.

**M1.5 YouTube search and merge.**
- `youtube.search` (D25): ytmusicapi, falling back to yt-dlp, in a threadpool.
- `rank_candidates` (§9.4), the `ys:` cache, the `in_library` and `track_id` lookup in `sources`, and the full `/api/search` response with `remote=auto|always|never`.
- **Tests:** `test_rank.py`.
- **Acceptance:**
  - A miss returns YouTube results in under 2 s the first time.
  - Repeating the same query is served from Redis in under 100 ms.
  - A result whose video is already in the database comes back with `in_library: true`.

**M1.6 Track rows.** `ensure_track` (D13), using `parse_title` on the hint and the artist upsert rules in §5.1. Also the Track serializer (`cache_state` per D14, `art_url`, `liked`), `POST /api/tracks/ensure`, `GET /api/tracks/{id}`, `GET /api/art/{kind}/{id}`, and video_id validation (D12).
- **Acceptance:** calling `ensure` twice with the same `video_id` returns the same `id`, and no extra artist rows appear.

**M1.7 Streaming.** `request_download` (D27) and `GET /api/stream/{video_id}`: a cache hit is served with `FileResponse` and updates `last_played` (D29); a miss goes through the live proxy (D30). Also `POST /api/tracks/{video_id}/cache`.
- **Acceptance:**
  - An uncached video produces sound in under 2.5 s, and seeking works.
  - Two simultaneous requests for the same uncached video create one `download_jobs` row and one arq job.

**M1.8 Download worker.** `download_track` (§9.5), retry and permanent-error handling (D28), the `feed_backlog` cron (D27), and `evict` plus its hourly cron (D33).
- **Acceptance:**
  - After a live play, the file appears at the D16 path, and the `files` row has a codec, a byte size, `loudness_lufs` and `gain_db`.
  - The artwork JPEG exists.
  - The m4a tags show clean metadata.
  - A private video fails without retrying.
  - Setting `STORAGE_LIMIT_GB=0.001` in a test run evicts unpinned files, keeps liked ones, and leaves the track rows in place.

**M1.9 WebSocket hub.** `/ws` (§8): connect handling, `state`, pub/sub forwarding (D39), `track_cached` / `download_progress` / `download_failed`, storing and relaying `player_state` and `queue_update`, `devices` broadcasts, and the active-device rule (D35).
- **Acceptance:**
  - With two browser tabs open on one profile, starting playback in tab B pauses tab A.
  - Both tabs receive `track_cached`.

**M1.10 Bare UI for the core loop.**
- Vite + React + TypeScript + Tailwind setup and the `vite.config.ts` proxy (D6).
- A bare page: a hard-coded profile (or the first one), a search box, the songs and YouTube lists, one `<audio>` element, and a live cache indicator driven by `track_cached`.
- **Acceptance (the doc §13 core loop, run by hand and recorded in `tracking.md`):**
  1. On a fresh database, search for "tum hi ho". The result is a miss, and YouTube results appear in under 2 s.
  2. Play the top result. Sound starts in under 2.5 s.
  3. Within about 20 s, the row turns to cached.
  4. Reload, then search again. The result is a hit, served in under 80 ms.
  5. Play it. Sound starts in under 300 ms.
  6. With the network to YouTube blocked, the cached track still plays.

### Phase 1B: Every P0 feature

> **Backend track (D48):** only the `-B` parts below are built now. Their text for the client (`-F`) is the spec for later.

**M1.11 App shell.** *[FRONTEND, deferred]* Routing (§10.1), the layout (sidebar on wide screens, bottom tabs on phones), theme tokens and the dark/light switch, the API client with the profile header, the profile picker and creation (`/profiles`, which keeps its choice in `localStorage`), and the `ws.ts` client with reconnects. Replace the M1.10 bare page.
- **Sidebar contents:** Home, Search, Your Library, then Liked Songs, then playlists with `pinned_to_sidebar` first and the rest after, most recently played first.
- **Web image:** multi-stage Dockerfile and `nginx.conf` (D6).
- **Acceptance:**
  - `docker compose up` serves the app at `:3000`, with `/api` and `/ws` proxied.
  - The theme follows the system setting and can be overridden.

**M1.12 Search UI.** *[FRONTEND, deferred]*
- Debounced suggestions while typing (250 ms) and a full search on submit.
- A top-result card, the Songs, Artists, Albums and Playlists tabs, and a "From YouTube" section with cloud and spinner indicators.
- A "More from YouTube" button that sends `remote=always`, and Ctrl/Cmd+K to focus search.
- Each track row has a menu with Play, Play next, Add to queue, Like and Add to playlist.
- **Acceptance:** suggestions appear while typing, and a "Not in library" song can be played, queued, liked or added to a playlist straight from the results.

**M1.13-B History API (backend, now).** `POST /api/me/history` takes an array (≤ 500) of `{track_id, played_at, ms_played, skipped, context, device_id}` and returns 204 (D38). A play is *counted* when `ms_played ≥ min(30 000, 50 % of the track's duration_ms)`; with no known duration the 30 000 ms rule applies. `user_track_stats` is upserted in the same transaction (`play_count += counted`, `skip_count += skipped`, `last_played`). Unknown `track_id` → 422 for the whole batch; `context` must be one of the D20 forms.
- **Tests:** counted threshold at both boundaries and for short tracks, a batch of several tracks, upsert accumulation, skipped, unknown track rejected with nothing written, other profiles' stats untouched.
- **Acceptance:** after a batch the search `personal` component and `/api/me/recent` (M1.14-B) reflect it.

**M1.13-F Player.** *[FRONTEND, deferred]*
- `queue.ts` and `shuffle.ts`, with tests.
- `engine.ts` (a single element, D36), the Now Playing bar (artwork, title, like, controls, seek bar, volume, queue button), and a queue panel with dnd-kit reordering in both sections.
- History reporting (D38, including `sendBeacon`), pre-caching the next two tracks (D34), Media Session (§10.4), keyboard shortcuts (§10.4), and restoring state from the WebSocket on load.
- **Tests:** `queue.test.ts` and `shuffle.test.ts`.
- **Acceptance:**
  - Lock-screen and media-key controls work on Android Chrome and macOS, and on iOS Safari if available.
  - After a reload, the last track and position come back, paused.
  - With shuffle on, the same artist doesn't play twice in a row whenever that's avoidable.

**M1.14-B Library API (backend, now).**
- Likes: `PUT`/`DELETE /api/me/likes/{track_id}` (204, liking requests a low-priority download, D19), `GET /api/me/likes?offset=&limit=` newest first.
- Playlists: `GET`/`POST /api/playlists`, `GET`/`PATCH`/`DELETE /api/playlists/{id}`, `PUT`/`DELETE /api/playlists/{id}/cover` (ffmpeg re-encode to 600×600 JPEG, ≤ 5 MB, D45), `POST`/`DELETE`/`PATCH /api/playlists/{id}/tracks` (add at `end`/`start`, remove by `item_id`, move before an `item_id` or to the end; numeric positions per D18; adding requests low-priority downloads). Playlists are owned: another profile gets 404. `GET /api/playlists/{id}` pre-caches its first five uncached tracks at low priority (D19). Move `playlist_json` out of `routes/search.py` into the playlist module.
- `GET /api/me/library` → `{liked_count, playlists}`; `GET /api/me/recent?limit=` → distinct contexts from history with `name`, `art_url`, `last_played` (D20).
- **Tests:** like/unlike idempotent and download requested, liked file is pinned (survives eviction, D17), playlist add/remove/move including the same track twice, positions after many inserts at the start, cover upload re-encode and reject non-images, ownership, `track_count`/`collage`, pre-cache creates `download_jobs`, `recent` dedupes by context.
- **Acceptance:** opening a playlist pre-caches its first five uncached tracks at low priority (check `download_jobs`); liking a track pins its file.

**M1.14-F Library.** *[FRONTEND, deferred]*
- Like and unlike, and the Liked Songs page.
- Playlists: create, rename, description, delete, add, remove, drag-to-reorder, cover upload, and a 2×2 collage cover built with CSS from the first four artworks.
- **Playlist page header:** the cover, name, description and owner, plus **Play** and **Shuffle play** buttons and a menu with Edit, Pin to sidebar and Delete. The Download toggle arrives in M4.2 and the colour gradient in M2.5.
- **Per-track menu** (the same component everywhere): Play next, Add to queue, Like/Unlike, Add to playlist, and Remove from this playlist when inside a playlist. Go to artist and Go to album are added in M3.1, and Fix metadata in M2.3.
- The Library page: grid and list views, sorting by recent, alphabetical or creator, Liked Songs pinned at the top, and the Playlists filter chip.
- The Home page, Phase 1 version: a greeting, a Liked Songs tile, a "Recently played" row (`/api/me/recent`), and playlist tiles.
- **Acceptance:** opening a playlist pre-caches its first five uncached tracks at low priority (check `download_jobs`). Liking a track pins its file, so eviction skips it.

**M1.15-B Settings API (backend, now).** `GET /api/settings` → `{codec, storage_limit_bytes, storage_used_bytes, file_count}`, read-only (D37). **Acceptance:** the figures equal `SUM(files.bytes)` and the limit equals `STORAGE_LIMIT_GB` in bytes (as GiB).

**M1.15-F Settings.** *[FRONTEND, deferred]* Theme, storage used versus the limit, and the codec, all read-only except theme (D37). Device name (D21). **Account:** the profile's name and avatar (with no login, the profile is the account), plus Switch profile. Settings gains crossfade, gapless, normalization and EQ in M2.4, devices in M4.1, and import in M3.4.
- **Acceptance:** the storage figures match `SUM(files.bytes)`.

**Phase 1 exit check (backend version, D48):**
- Every **P0** row in Appendix A has its server side working and tested (copy the rows into `tracking.md` as a checklist; mark the client half "deferred").
- The server-side doc §12 performance targets are measured on the target hardware, or on a laptop if the Pi isn't available yet: suggest under 30 ms, search hit under 80 ms, search miss under 2 s, cached first sound under 300 ms, uncached first sound under 2.5 s, background download 5–20 s.
- All tests pass.

### Phase 2: Polish

**M2.1 Metadata enrichment** (doc §9.1 stages 4–6), run inside `download_track` after parsing.
- Search `ytmusicapi` (songs filter) for `"{artist} {title}"`. Accept the first result whose duration is within ±10 s **and** whose normalized title has a `difflib` ratio of at least 0.5 (the second guard is an addition, to stop same-length wrong matches).
- On a match:
  - upsert artists by `ytm_channel_id`. If a provisional artist row with the same `name_norm` exists, merge it into the channel row with `merge_artists`, which re-points `tracks`, `track_artists` and `albums` and then deletes the orphan.
  - fetch the album with `get_album(browseId)` to get the title, year and the largest square cover, saved to `art/album/<id>.jpg`.
  - set `metadata_status = 'enriched'` and call `refresh_track_search`.
- Leave `manual` rows alone.
- Add a one-off command, `python -m app.tools.reenrich`, for tracks downloaded in Phase 1.
- **Tests:** a `merge_artists` test.
- **Acceptance:** "arijit singh official" and "Arijit Singh" end up as one artist, and enriched tracks show square album art.

**M2.2 Lyrics.** **[SKIPPED, D49]** *(original text kept for reference.)* *[Backend now: the migration, LRCLIB client, prefetch and `GET /api/tracks/{id}/lyrics` with tests. Client LRC parser and karaoke view: FRONTEND, deferred.]* Migration `002` (the lyrics table).
- Fetch from LRCLIB's `/api/get` with artist, title, album and duration (in seconds), falling back to `/api/search`. Send a `User-Agent` of `Resonance/<version>`. Cache the answer, including "not found" (§5.2).
- Prefetch lyrics in the worker after a download.
- Client: an LRC parser (with a test), a karaoke view that scrolls automatically and seeks when a line is tapped, a plain-text fallback, and the Y shortcut.
- **Acceptance:** a known song shows synced lines that stay in time after seeking.

**M2.3 Fix metadata.** *[Backend now: `PATCH /api/tracks/{id}`, `POST /api/tracks/{id}/sources` and a new `GET /api/tracks/{id}/candidates` returning the ranked YouTube alternatives; the dialog is FRONTEND, deferred.]* A dialog that edits the title, artists, album and year (`PATCH /api/tracks/{id}`, which rewrites the tags), and lets you choose a different source (`POST /api/tracks/{id}/sources`). The dialog shows the top `rank_candidates` results for the track as choices.
- **Acceptance:** after switching source, the track ID stays the same, playlists still work, and the old file is gone.

**M2.4 Web Audio engine** (D36). *[FRONTEND, deferred. The server already supplies `gain_db`.]*
- The two-element graph; normalization from `gain_db` (`prefs.normalize`); crossfade over 0–12 s with linear `GainNode` ramps (`prefs.crossfade_s`).
- Near-gapless playback when the next track is from the same album in an album context, in which case crossfade is skipped.
- A 10-band EQ at 31, 62, 125, 250 and 500 Hz and 1, 2, 4, 8 and 16 kHz: the outer bands are shelf filters and the rest are peaking filters with Q of about 1.4, with gain from −12 to +12 dB. Presets: Flat, Bass boost, Treble boost, Vocal, Rock, Electronic.
- A sleep timer (5–90 minutes, or end of track), done in the client.
- Playback speed from 0.5× to 2× using `playbackRate` with `preservesPitch`.
- The engine's interface doesn't change, so `queue.ts` and the UI stay as they are.
- **Acceptance:**
  - Crossfade can be heard and has no clicks.
  - Volume is even across loud and quiet tracks.
  - Background and lock-screen playback still work on Android and iOS. If iOS breaks, apply the D36 fallback.

**M2.5 Search history, PWA, colour and full-screen player.** *[Backend now: the recent_searches table and `GET`/`POST`/`DELETE /api/me/searches`. PWA, colour extraction and the full-screen player: FRONTEND, deferred.]* The table goes in the next free migration number (see §5.2).
- **Recent searches:** a list on the empty search page, clearable, recorded when a search result is opened or played.
- **PWA:** `vite-plugin-pwa` with a manifest, icons and an app-shell precache. The service worker **never** caches `/api` or `/ws`.
- **Dominant colour:** a canvas function that samples an average of saturated pixels from a local `/api/art` URL. It drives the playlist and album header gradients and the full-screen player background.
- **Full-screen player:** large artwork, a lyrics panel, and swipe gestures on phones (left/right for next and previous, down to close) using pointer events.
- **Acceptance:** the app can be installed on Android and desktop Chrome, and added to the iOS home screen. The app shell loads offline.

### Phase 3: Discovery

**M3.1 Artist and album pages.** *[Backend now: endpoints, follows, saved albums (`radio_seed` dropped, D50). Pages: FRONTEND, deferred.]* Migration `003` (saved_albums and follows).
- Use ytmusicapi `get_artist` and `get_album`, cached in Redis (§6.1), with the D42 ID forms.
- **Artist page:** hero image (cached locally to `art/artist/<id>.jpg`), "your plays (30 days)", Popular tracks (YouTube Music top songs merged with your own play counts), Discography, Fans also like (YouTube Music related artists), and About (bio).
- **Album page:** the header gradient, **Play**, **Shuffle play** and **Save** buttons, and a track list. Tracks not yet in the library show as RemoteTrack rows; pressing play ensures the whole album.
- Follow an artist and save an album. Add the Artists and Albums library chips, and YouTube artist and album results in the search tabs (D25).
- **Acceptance:** opening an artist from YouTube search gives a page with a database ID, and following it shows the artist under Library → Artists.

**M3.2 Radio and autoplay.** **[SKIPPED, D50; autoplay alone was brought back as D51, built on web recommendations instead of YouTube Music radio]** *(original text kept for reference.)* *[Backend now: `/api/radio/{track_id}`. The radio context and autoplay in the client: FRONTEND, deferred.]*
- `/api/radio/{track_id}`: ytmusicapi `get_watch_playlist(videoId, radio=True)`, cached in Redis for 1 h. Ensure all the results, then re-rank:
  - +1 for liked tracks
  - −1 when `skip_count ≥ 2` and `skip_count > play_count`
  - no more than one track per artist in any window of five
- Return 25 tracks, excluding the IDs in `exclude`.
- **Artist radio** (doc §2 "song / artist radio") uses the same endpoint. Its seed is the artist's top track: your most-played track by them, or else the first of YouTube Music's top songs, ensured first. There's no separate endpoint. Artist pages get a **Radio** button.
- **Client:** a radio context (`radio:<id>`) that fetches the next batch when three tracks remain (doc §8.2). Autoplay: when the context ends with repeat off and `prefs.autoplay` on, start a radio from the last track.
- **Acceptance:** a radio never runs out, and the same artist doesn't appear twice within any five tracks.

**M3.3 Home feed** *[backend now; the Home page is FRONTEND, deferred]* (`/api/home`, doc §9.2 and §10):
- quick-access tiles: Liked Songs plus the five most recent contexts
- Jump back in: recent distinct contexts
- On repeat: top tracks by counted plays over the last 30 days
- Recently added: newest `files.created_at`
- ~~Made for you: radios seeded from the top three On repeat tracks~~ (skipped, D50); Daily Mixes (Phase 5) create this row
- An empty section is hidden.
- **Acceptance:** each section is built from the data that defines it (check with known history).

**M3.4 Playlist import** (D41). *[Backend now; the dialog is FRONTEND, deferred.]* Migration `003` (imports).
- `POST /api/import` creates the playlist straight away and queues `run_import`.
- `run_import` works through the rows: YouTube URLs map directly to video IDs, and CSV rows go through a ytmusicapi search with a duration check of ±10 s, then `rank_candidates`. Each match is ensured and added to the playlist in order, and its download is requested at low priority. Sleep 0.5 s between searches. Send `import_progress` and store unmatched rows.
- Client: an import dialog in Settings, a progress bar, and a list of unmatched rows.
- **Acceptance:** a 100-row Exportify CSV turns into a playable playlist within minutes. Downloads trickle through the backlog without delaying tracks that are actually being played.

### Phase 4: Devices and operations

**M4.1 Connect.** *[Backend now: the `transfer` WebSocket message and `GET /api/devices`. Device picker and "Playing on X" bar: FRONTEND, deferred.]*
- A device picker in the Now Playing bar, using the online and active state from `devices`.
- The `transfer` message (§8), and a "Playing on X" bar with play and pause relayed through `transfer` back to the current device. Remote control without transferring is out of scope (doc §2 only lists transfer).
- **Acceptance:** start on a phone, transfer to the laptop, and playback continues within about 1 s of the same position.

**M4.2 Offline downloads.** *[Backend now: migration, `PUT`/`DELETE /api/me/downloads/{playlist_id}`, eviction pinning. IndexedDB storage and offline playback: FRONTEND, deferred.]* Migration `004`.
- A "Download" toggle on a playlist calls `PUT /api/me/downloads/{playlist_id}` (pinning its files, D17). The client then fetches each track's `/api/stream/<video_id>` in full and stores the blob and its Track JSON in IndexedDB (`idb-keyval`).
- Offline playback uses object URLs. Offline search covers the downloaded Track JSON only. Plays are kept in IndexedDB and sent to `/api/me/history` when the connection returns (doc §8.4).
- Add the Downloaded library chip and show storage use per device.
- **Acceptance:** in airplane mode, the installed PWA plays downloaded playlists and the plays sync once back online.

**M4.3 Household profiles.** *[Backend now: the per-profile isolation review as tests. Avatar picker and menu: FRONTEND, deferred.]* Avatar picker, profile switching from the menu, and a per-profile isolation review: likes, history, prefs, queue and recent searches must never leak between profiles.
- **Acceptance:** two profiles on one device each keep their own likes, recents and queue.

**M4.4 Admin page** (`/admin`). *[Backend now: `/api/admin/*` endpoints and the update job. The page: FRONTEND, deferred.]*
- download queue counts by status, with live progress
- failed jobs with Retry and "Try next-best source"
- disk usage against the limit, plus the over-budget warning
- yt-dlp version and an Update button

**Update** runs the `update_ytdlp` job in the worker and the same command in the API process (`uv pip install -U "yt-dlp[default]"`). Each process then exits so Docker's `restart: unless-stopped` brings it back on the new version. The image keeps its build-time version, so also rebuild weekly (doc §11). Document this in `tracking.md`.
- **Acceptance:** retrying a failed job moves it to `done`, and the update button changes the version shown.

**M4.5 Backups.** `scripts/backup.sh`: `docker compose exec -T db pg_dump` to a gzipped file in `./backups/`, keeping the newest 14. Document a host crontab line for running it nightly, and an optional `rsync` of `audio/` (doc §11).
- **Acceptance:** a restore into a fresh volume gives back the library.

### Phase 5: Extras (any order, P2)

- **M5.1 Browse by genre and mood:** the empty search page shows YouTube Music's "Moods & genres" tiles (`get_mood_categories`, cached for 24 h). A tile opens its playlists (`get_mood_playlists`). Opening a playlist loads its tracks with `get_playlist`, and they play like an album from YouTube: the tracks are ensured when you press play. Nothing is stored per track (D47).
- **M5.2 Daily Mixes:** at 04:00 each day, take the profile's top 30 artists from the last 90 days and group them greedily. The highest-ranked unassigned artist seeds a group, which also takes every other top artist in the seed's YouTube Music related artists (D47). The first six groups become mixes. Each mix is 50 tracks: 25 of your top tracks by the group's artists and 25 top songs from the seed's related artists that you haven't played yet. These are ensured, downloaded at low priority, stored in `mixes`, and shown in "Made for you". Grouping by related artists is the deliberately simple version of the doc's "co-listening and genre tags" clustering.
- **M5.3 Discover Weekly:** on Sundays at 22:00, collect the YouTube Music related artists of the top 20 artists, drop any artist already played, and take the top song of each (30 tracks at most). Ensure them and pre-cache them at low priority (doc §9.2).
- **M5.4 Release Radar:** a weekly `get_artist` check on followed artists with a `ytm_channel_id`. Albums and singles that are new since the last check go into `new_releases` and the home row "New from followed artists".
- **M5.5 Stats and Wrapped:** `/api/me/stats` gives top tracks, top artists and minutes listened from `play_history`. `/api/me/wrapped?year=` feeds tap-through story cards in the client.
- **M5.6 Collaborative playlists and folders:** `playlist_members` with edit rights, and nested folders in the sidebar.
- **M5.7 Chromecast** *[FRONTEND only, deferred]*: Cast Web Sender SDK with the default media receiver (Chrome only). The stream URL must be an absolute LAN URL that includes `?profile=`. DLNA is skipped.
- **M5.8 Canvas-style background** *[FRONTEND only, deferred]*: an animated CSS gradient built from the extracted palette, shown in the full-screen player.
- **M5.9 MinIO (optional):** when `S3_ENDPOINT` is set, the worker uploads files to the bucket using the same relative keys, and a cache hit returns a 302 to a presigned URL (MinIO handles Range). Pick the client library at that point and add it to D2.

### Phase 6: Authentication (TBD)

Replace `current_profile` (D10) with session authentication: per-profile passwords or passkeys, an admin role, HTTPS through a reverse proxy, and a remote-access option such as a VPN. Plan this in detail when the phase starts.

---

## 12. Testing and verification

- **Python:** `server/tests/`, run with `uv run pytest`. Required: `test_text.py` (M1.2), `test_search_sql.py` with its fixture (M1.4; needs the compose database), `test_rank.py` (M1.5), the `merge_artists` test (M2.1), and the LRC and import CSV parsing tests when those features land.
- **Web:** run with `npm test` (vitest). Required: `queue.test.ts` and `shuffle.test.ts` (M1.13), and the LRC parser test (M2.2).
- **No other test suites** unless a bug needs one. A bug fix adds the smallest test that would have caught it.
- **Manual acceptance:** each milestone's acceptance list is run (pytest, `curl`, or a script for live YouTube steps) and its result noted in `tracking.md`, with dates and measured numbers. Steps that need a browser belong to the frontend track (D48).
- **Device matrix** *(frontend track, D48)*, at the end of Phases 1 and 2: desktop Chrome, Firefox and Safari; Android Chrome; iOS Safari, both in the browser and as an installed PWA.

---

## 13. Risks and open questions

| Risk or question | Mitigation or owner |
|---|---|
| YouTube changes break yt-dlp (bot checks, PO tokens, JS challenges) | Keep yt-dlp current (M4.4 and weekly rebuilds), ship Deno (D9), use the cookies file (D8), keep requests gentle (D27, D28). Check the yt-dlp wiki for PO-token providers if downloads start failing. |
| ytmusicapi is unofficial and can break | The yt-dlp search fallback (D25). Pin the version and upgrade alongside yt-dlp. |
| The live proxy is throttled by googlevideo | Measured in M0.2. Playback only needs real-time speed. |
| iOS Safari stops Web Audio in the background | D36 fallback, tested in M2.4 |
| Search threshold calibration | M1.4 fixture. Retune with real use and record it in D24. |
| Devanagari and other non-Latin text in trigram and tsvector | Most YouTube Music titles are romanized; the M1.4 fixture includes native-script cases. Revisit if search misses them. |
| macOS's case-insensitive filesystem in host development | Only video IDs that differ by letter case would collide, which is vanishingly unlikely. Containers use Linux filesystems. |
| **Open:** whether pasting Spotify URLs is wanted | D41. It needs Spotify API credentials. |
| Legal: personal use only (doc §1 usage note) | Run on the LAN only, never share links or files, and nothing public before Phase 6. |

---

## 14. Glossary

- **Hit / miss:** whether local search's top song score reaches `SEARCH_HIT_THRESHOLD` (§9.3).
- **Cached / remote:** whether a track has a file on disk (D14). A track can be in the library (it has a row) and still be `remote`.
- **Provisional track:** a row created from search metadata before any download (D13).
- **Preferred source:** the YouTube video a track streams from (D15).
- **Context:** where playback came from (D20). It drives "Jump back in" and the queue.
- **Backlog:** low-priority `download_jobs` rows that wait for `feed_backlog` (D27).
- **Active device:** the device of a profile that is currently allowed to play (D35).

---

## Appendix A. Feature checklist (complete list from the design doc)

> D48: a milestone cited here covers both halves. Right now only the `-B` (server) half is built; the `-F` half is the frontend track.

Priority comes from doc §2: **P0** is the first usable build, **P1** the second phase, **P2** nice-to-have. "Milestone" is where each feature is built.

| Area | Feature | How it works | Pri | Milestone |
|---|---|---|---|---|
| Search | Instant search with suggestions | Debounced local FTS + trigram as you type. YouTube fallback on submit or a weak match. | P0 | M1.4, M1.5, M1.12 |
| Search | Tabs: Songs, Artists, Albums, Playlists | Grouped local results. YouTube results are tagged "Not in library". | P0 | M1.4, M1.12. YouTube artist and album tabs in M3.1. |
| Search | Recent searches | Stored per profile, clearable | P1 | M2.5 |
| Search | Browse by genre / mood | YouTube Music "Moods & genres" (D47) | P2 | M5.1 |
| Playback | Play, pause, seek, next, previous | HTML5 Audio with HTTP Range | P0 | M1.7, M1.13 |
| Playback | Queue with drag-and-drop, Play next, Add to queue | Client reducer; state stored server-side and synced over WebSocket (D34) | P0 | M1.9, M1.13 |
| Playback | Smart shuffle; repeat one / all | Shuffle avoids the same artist back-to-back (§9.6) | P0 | M1.13 |
| Playback | Gapless playback and crossfade (0–12 s) | Web Audio, two elements, next track prefetched. Gapless is near-gapless (D36). | P1 | M2.4 |
| Playback | Volume normalization | EBU R128 measured at download time (D32), applied in the client | P1 | M1.8 (measure), M2.4 (apply) |
| Playback | 10-band equalizer + presets | Web Audio BiquadFilter chain | P1 | M2.4 |
| Playback | Sleep timer, playback speed | Client-side timer; `playbackRate` | P1 | M2.4 |
| Playback | Lock screen, media keys, headphone controls | Media Session API | P0 | M1.13 |
| Library | Liked Songs | One-tap heart. The file is pinned so it's never evicted (D17). | P0 | M1.14 |
| Library | Playlists: create, edit, reorder, cover, description | Postgres. Cover is a 4-track collage or an upload. | P0 | M1.14 |
| Library | Folders for playlists | Nested folder table | P2 | M5.6 |
| Library | Follow artists, save albums | Library tables; album pages from YouTube Music | P1 | M3.1 |
| Library | Recently played, Jump back in | `play_history`, deduplicated by context | P0 | M1.14 (Recently played), M3.3 (Jump back in) |
| Library | Import Spotify / YouTube playlists | YouTube URL or CSV upload; tracks matched and queued for download. Spotify is CSV-only (D41). | P1 | M3.4 |
| Discovery | Home feed (Made for you, Recently played) | Assembled server-side from history + recommendations | P1 | M3.3 |
| Discovery | Song / artist radio | **Skipped (D50)** | P1 | ~~M3.2~~ |
| Discovery | Daily Mixes, Discover Weekly | Grouping by YouTube Music related artists (D47) | P2 | M5.2, M5.3 |
| Discovery | Release Radar ("New from followed artists") | Weekly check of followed artists' YouTube Music pages (doc §9.2) | P2 | M5.4 |
| Discovery | Autoplay when the queue ends | Web search + LLM recommendations, regenerated on a hand-picked song and extended in the background (D51). **Backend done**; the client rules are in §10.3 (frontend deferred). | P1 | D51 (server), client deferred |
| Content | Synced lyrics (karaoke view) | **Skipped (D49)** | P1 | ~~M2.2~~ |
| Content | Artist pages: bio, top tracks, discography | YouTube Music artist page + your own plays | P1 | M3.1 |
| Content | Canvas-style animated background | Palette from artwork; animated gradient | P2 | M5.8 |
| Content | Fix metadata, choose a different source | Dialog (doc §9.1) | P1 | M2.3 |
| Devices | Continue on another device (Connect) | WebSocket presence; transfer playback between sessions | P1 | M1.9 (last play wins), M4.1 (transfer) |
| Devices | Offline mode | PWA service worker + IndexedDB downloads of chosen playlists | P1 | M2.5 (app shell), M4.2 (downloads) |
| Devices | Cast to speakers | Chromecast via the Cast SDK. DLNA is skipped. | P2 | M5.7 |
| Social | Multiple profiles (household) | Profile picker, no password, shared audio store | P1 | M1.3, M1.11 (basic), M4.3 (polish) |
| Social | Shared / collaborative playlists | Playlist members with edit rights | P2 | M5.6 |
| Social | Last.fm scrobbling | — | P2 | **Removed (D47)** |
| Insights | Listening stats and yearly Wrapped | Aggregates over `play_history`, shown as story cards | P2 | M5.5 |
| Settings | Audio quality, storage limit, cache policy | Server-wide codec (D37); storage budget with LRU eviction (D33) | P0 | M1.8, M1.15 |
| Settings | Dark / light theme, keyboard shortcuts | CSS tokens; shortcuts in §10.4 | P0 | M1.11, M1.13 |
| Ops | Admin page | Queue status, failed jobs, disk, yt-dlp version and update (doc §10) | — | M4.4 |
| Ops | Backups | Nightly `pg_dump`; optional rsync of audio (doc §11) | — | M4.5 |
| Ops | Authentication | Logins, access control, remote access | — | Phase 6 |

## Appendix B. Screen checklist (from doc §10)

> D48: every screen below belongs to the deferred frontend track. The server endpoints each screen needs are built under the `-B` milestones.

| Screen | Contents | Milestone |
|---|---|---|
| Profile picker | Pick or create a profile, with no password | M1.11 (avatars in M4.3) |
| Left sidebar (wide screens) | Home, Search, Your Library with filter chips (Playlists in M1.14, Artists and Albums in M3.1, Downloaded in M4.2), Liked Songs, playlists pinned to the sidebar | M1.11 |
| Bottom tabs (phones) | Home, Search, Library, with a mini player above | M1.11 |
| Home | Greeting, six quick-access tiles, rows: Jump back in, Made for you, On repeat, Recently added, New from followed artists | M1.14 (basic), M3.3 (full), M5.4 (New from followed artists) |
| Search | Search bar with live suggestions; recent searches (M2.5) and mood/genre tiles (M5.1) when empty; Top result card; Songs, Artists, Albums, Playlists; "From YouTube" section with download indicators | M1.12 |
| Library | Grid or list, sort by recent, alphabetical or creator, Liked Songs pinned at the top | M1.14 |
| Liked Songs | Liked tracks, newest first | M1.14 |
| Playlist | Header with cover and dominant-colour gradient (M2.5); Play, Shuffle play and Download (M4.2) buttons; track list with drag-reorder; per-track menu | M1.14 |
| Album | Same header pattern with Play, Shuffle play and Save; track list; per-track menu | M3.1 |
| Artist | Hero image, your plays (30 days), Popular tracks, Discography, Fans also like, About, Follow and Radio buttons | M3.1, M3.2 |
| Now Playing bar | Artwork, title, like button, controls, progress, volume, queue; lyrics (M2.2), devices (M4.1), full-screen (M2.5) | M1.13 |
| Full-screen player | Large artwork on a colour gradient (static in M2.5, animated in M5.8), lyrics panel, swipe gestures on phones | M2.5 |
| Lyrics view | Synced lines that scroll automatically; tap a line to seek | M2.2 |
| Queue panel | Now playing, Next in queue (user-added), Next from context; drag to reorder | M1.13 |
| Settings | Theme, storage usage and limit, codec (read-only), account (profile name and avatar), device name; then crossfade, gapless, normalization and EQ (M2.4), devices (M4.1), import (M3.4) | M1.15 |
| Admin | Download queue status, failed jobs with retry and next-best source, disk usage, yt-dlp version and update button | M4.4 |
| Download state indicator | On every track row: no icon (cached), spinning ring with % (downloading), cloud (not in library), warning (failed) | M1.12 (D14) |

# Очередь задач

Каждая строка — одна задача на один день/коммит.
Формат: "- [ ] область: что сделать" (после выполнения daily_dev.py сам меняет на "[x]").

- [x] scanner: parse ID3/FLAC tags with mutagen, write to tracks table (SQLite)
- [x] scanner: handle edge cases (missing tags fallback to filename, OGG/Opus/M4A support)
- [x] scanner: extract embedded cover art (APIC/FLAC picture) and cache to static/covers

- [x] db: add source field ('local'|'remote'), external_id and cache_path to tracks schema
- [x] providers: create base StreamSource abstract class with get_stream_url and get_metadata
- [x] providers: implement LocalStorageProvider reading files from disk
- [x] providers: implement RemoteDiscoveryProvider using ytmusicapi for global track search
- [x] providers: implement remote stream extraction using yt-dlp to get direct audio URLs

- [x] api: GET /tracks with pagination, sorting and filter by source (local/remote)
- [x] api: GET /search?q= querying both local database and remote discovery provider
- [x] api: implement GET /stream/{track_id} with local FileResponse and remote 307 redirect
- [x] api: implement POST /tracks/remote to persist external tracks into SQLite before playback
- [x] favorites: POST /tracks/{id}/like toggling favorite status in database
- [x] cache: implement LRU disk cache manager for remote tracks (auto-evict oldest unliked files)

- [x] frontend: HTML5 responsive player layout (sidebar, tracklist view, bottom player dock)
- [x] frontend: dark theme CSS via custom properties (--bg-primary, --bg-surface, --accent, --text-primary, --text-muted) applied consistently across sidebar/tracklist/player dock; track cards get a hover overlay (play icon fade-in, slight scale) and an "active/playing" state with an accent left-border
- [x] frontend: volume slider (range input 0-100) wired to `audio.volume`, mute toggle icon button, both persisted to localStorage (`player:volume`, `player:muted`) and restored on page load
- [ ] frontend: search bar with 300ms debounce calling GET /search?q=; replaces the tracklist view with results grouped by source ("Локально" / "Найдено"), with empty-state and loading-state
- [ ] frontend: player dock shows album artwork (cover_path or placeholder), elapsed/total time formatted mm:ss, progress bar synced to `audio.currentTime` via the `timeupdate` event, draggable to seek
- [ ] frontend: playback queue module (array + index pointer) exposing next()/previous()/playAt(i); auto-advances to the next queued track on the `ended` event, no-ops at the end of the queue
- [ ] frontend: heart/like button in the player dock calling POST /tracks/{id}/like, toggling filled/outline state optimistically and reconciling with the API response
- [ ] frontend: "Любимые треки" section in the sidebar rendering GET /favorites results with the same track card component as the main list

- [ ] cache: background task (FastAPI BackgroundTasks) triggered on the first remote stream request — downloads the track via yt-dlp into the cache dir, updates tracks.cache_path when done; subsequent requests for the same track_id are served from disk instead of redirecting again
- [ ] api: GET /favorites — tracks WHERE liked = 1 regardless of source, same response shape as GET /tracks
- [ ] history: POST /history {track_id, event: play|skip|finish}; "skip" is only accepted if elapsed_seconds < 15, otherwise rejected with 400
- [ ] history: GET /history?limit=50 — most recent play/finish events joined with track metadata, ordered by timestamp desc

- [ ] wave: WaveEngine.generate_candidates(seed_track_ids) using ytmusicapi's get_watch_playlist radio per seed track, deduped, returned with source metadata attached
- [ ] wave: candidate filter excluding track_ids played within the last N days OR skipped 2+ times, applied before scoring
- [ ] wave: hybrid scorer mixing 60% remote radio candidates / 40% locally-liked library tracks into one ranked batch, mix ratio as a named constant
- [ ] wave: skip-streak adaptation — track consecutive skips in session state; on the 2nd consecutive skip, drop the current genre cluster from the candidate pool and inject one known-liked local track as a "safe" pick
- [ ] wave: mood presets (energy/focus/calm) as tempo + acoustic-feature filters on top of the ranked batch (needs a track_features table — add that migration first if it's missing)
- [ ] api: GET /wave/next?mood=&limit=5 returning a personalized batch from WaveEngine, session-scoped so repeated calls don't repeat already-served candidates
- [ ] frontend: "Моя Волна" button in the sidebar — on click, fetches /wave/next, loads the batch into the queue and starts playback; player dock shows a subtle animated waveform bar while a Wave-sourced track is playing

- [ ] db: playlists(id, name, created_at) and playlist_tracks(playlist_id, track_id, position) tables, position as an integer index, unique(playlist_id, track_id)
- [ ] playlists: POST /playlists {name} and GET /playlists listing the user's playlists with track_count; tracks may be local or remote (track_id is source-agnostic)
- [ ] playlists: POST /playlists/{id}/tracks {track_id} appends at the end (position = max+1); DELETE /playlists/{id}/tracks/{track_id} removes the entry and re-indexes remaining positions
- [ ] api: GET /tracks/{id}/download streaming the file with a Content-Disposition: attachment header (local tracks only; remote, not-yet-cached tracks return 404 with a clear message)
- [ ] frontend: playlist drawer in the sidebar listing playlists, "+ Новый плейлист" opens a modal (name input, create button, calls POST /playlists)
- [ ] frontend: context menu on track cards with "Добавить в плейлист" (submenu of existing playlists) and "Скачать на диск" (calls the download endpoint above)

- [ ] frontend: MediaSession API — set metadata (title/artist/artwork) and action handlers (play/pause/previoustrack/nexttrack) so OS lock-screen/media-key controls work and playback continues with the screen locked
- [ ] frontend: global keyboard shortcuts — Space (play/pause, ignored while focus is in an input), ArrowLeft/ArrowRight (seek ±5s), L (like current track), N (next track)
- [ ] frontend: mobile layout — player dock becomes a swipeable bottom sheet (collapsed mini-bar by default, swipe up for full view), sidebar becomes a slide-over drawer below 768px
- [ ] frontend: toast notification component for API errors (failed search, failed like, network error) — non-blocking, auto-dismisses after ~4s
- [ ] frontend: infinite scroll / "load more" pagination on the main tracklist instead of loading everything at once
- [ ] frontend: PWA manifest.json (name, icons, theme_color) + a minimal service worker caching the app shell, so the player is installable on a mobile home screen

- [ ] auth: users(id, username, password_hash) table with bcrypt hashing on registration; plaintext password is never stored or logged
- [ ] auth: signed session tokens issued on login, FastAPI Depends() dependency validating the token from a cookie/header and injecting current_user into protected routes
- [ ] auth: frontend login modal (username/password form) + "Продолжить как гость" fallback allowing local playback while disabling likes/playlists/history until logged in

- [ ] api: global exception handler returning structured JSON errors ({"error": "..."}) instead of raw tracebacks, with correct status codes
- [ ] api: GET /health returning {"status": "ok", "db": bool} for uptime checks and the systemd timer to alert on
- [ ] providers: wrap yt-dlp/ytmusicapi calls with a timeout + retry (max 2 attempts, exponential backoff) and a graceful empty-result fallback on failure, logged as a warning rather than raised
- [ ] ops: nightly SQLite backup script (copies tracks.db with a timestamp suffix into backups/, keeps the last 7), wired into its own systemd timer
- [ ] config: pydantic Settings class loading LIBRARY_PATH, CACHE_DIR, CACHE_MAX_SIZE_GB etc. from .env, replacing hardcoded paths in providers/cache modules
- [ ] config: ENABLE_REMOTE_SOURCE feature flag — when false, remote search/stream endpoints return 403 and the frontend hides remote results, so the app can run in local-library-only mode for demos or the defense

- [x] tests: unit tests for LocalStorageProvider and RemoteDiscoveryProvider mocks
- [ ] tests: integration test for POST /tracks/remote — mocks RemoteDiscoveryProvider, asserts the track is persisted with source='remote' and external_id, and is retrievable via GET /tracks
- [ ] tests: unit tests for WaveEngine scoring — verify the 60/40 mix ratio, the skip-streak genre-shift logic, and the recently-played exclusion filter
- [ ] ci: GitHub Actions workflow running `pytest -q` on every push to main, badge added to README
- [ ] docker: Dockerfile (python:3.12-slim base, ffmpeg via apt, copies the app, runs uvicorn) with tracks.db and the cache dir declared as VOLUMEs
- [ ] docker: docker-compose.yml wiring the app service, persistent volumes for tracks.db and cache/, and env vars for the library path
- [ ] docs: README.md with an architecture diagram (providers/cache/wave/api/frontend overview), setup/quickstart instructions and 2-3 screenshots
- [ ] frontend: fix track-item layout grid and spacing between duration and like button
- [ ] backend: save remote track duration on search and registration so it shows immediately
- [ ] backend: stream and cache remote audio files locally to disk for instant replay

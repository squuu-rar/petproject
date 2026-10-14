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
- [x] frontend: dark theme CSS via custom properties (--bg-primary, --bg-surface, --accent, --text-primary, --text-muted)
- [x] frontend: volume slider (range input 0-100) wired to `audio.volume`, mute toggle icon button, persisted to localStorage
- [x] frontend: search bar with 300ms debounce calling GET /search?q=; replaces the tracklist view with results grouped by source
- [x] frontend: player dock shows album artwork, elapsed/total time formatted mm:ss, progress bar synced to `audio.currentTime`
- [x] frontend: playback queue module (array + index pointer) exposing next()/previous()/playAt(i)
- [x] frontend: heart/like button in the player dock calling POST /tracks/{id}/like
- [x] api: GET /favorites — tracks WHERE liked = 1 regardless of source, same response shape as GET /tracks
- [x] frontend: "Любимые треки" section rendering GET /favorites results
- [x] cache: background task downloading remote tracks via yt-dlp into the cache dir and updating tracks.cache_path
- [x] history: POST /history {track_id, event: play|skip|finish}; "skip" is only accepted if elapsed_seconds < 15
- [x] history: GET /history?limit=80 — most recent play/finish events joined with track metadata
- [x] wave: WaveEngine.generate_candidates(seed_track_ids) using ytmusicapi's get_watch_playlist radio per seed track
- [x] wave: integrate Last.fm collaborative filtering into recommendation candidates
- [x] search: SoundCloud search and audio stream extraction via yt-dlp
- [x] frontend: "Моя Волна" single-track hero view with infinite stream prefetching
- [x] wave: candidate filter excluding track_ids played within the last N days OR skipped 2+ times, applied before scoring
- [x] wave: hybrid scorer mixing 60% remote radio candidates / 40% locally-liked library tracks into one ranked batch
- [x] api: GET /artists/{name} — local data only: {name, avatar, track_count, tracks[]}; case-insensitive Cyrillic matching and play count sorting
- [x] api: enrich GET /artists/{name} with a remote avatar via Last.fm artist.getInfo and ytmusic search(filter='artists') fallback with 4s timeout and in-memory cache
- [x] lyrics: LRCLIB integration in lyrics.py and GET /tracks/{id}/lyrics endpoint to fetch synchronized LRC lyrics (syncedLyrics) with fallback to plain lyrics
- [x] frontend: toast notification component for errors — non-blocking, auto-dismisses
- [x] tests: unit tests for LocalStorageProvider and RemoteDiscoveryProvider mocks
- [x] frontend: fix track-item layout grid and spacing between duration and like button
- [x] backend: save remote track duration on search and registration so it shows immediately
- [x] backend: stream and cache remote audio files locally to disk for instant replay

- [ ] frontend: artist profile view in the app's own hi-fi style (keep all existing styles in style.css): large round avatar, artist name in the display font (Unbounded), mono caption "N ТРЕКОВ", a vermilion «Слушать» key that plays the artist's tracks as a queue via playFromList, and the popular tracks as the standard track table (createTrackRow); no new nav item — the view opens on top of the current section
- [ ] frontend: make the artist name clickable everywhere (track rows, search results, player dock, wave deck) and open the artist view by fetching /artists/<encodeURIComponent(name)>; call e.stopPropagation() in track rows so the click does not start playback; keep keyboard access (the name is a button or link with focus-visible)
- [ ] frontend: back navigation for the artist view — hash route #/artist/<name>, a «← Назад» ghost button, browser back/forward support, and restoring the previous section (library, favorites or search query) when returning
- [ ] frontend: synced karaoke lyrics overlay (fetching /tracks/{id}/lyrics) with live active-line tracking via audio.timeupdate, smooth center-scroll and line-click seek; open it from a lyrics key in the player dock and from the secondary key row of the wave deck (next to the copy key); style it in the app's own hi-fi look: bone text on graphite, the active line in the vermilion accent, mono timestamps

- [ ] frontend: loading state for slow first plays — spinner on the play keys (dock and wave deck) while audio fires waiting/loadstart, removed on playing/canplay; on an audio error retry the same track once after 3 seconds before auto-skipping, so a track that is still being downloaded in the background is not skipped
- [ ] cache: prefetch the next 2 tracks of the queue in the background — POST /cache/prefetch {track_ids} in main.py calls CacheManager.get_or_download without waiting, skip local tracks and tracks that already have cache_path
- [ ] tests: SearchOrchestrator deduplication in provider/search tests — a SoundCloud track already saved in the DB (also with source='remote'), repeated URLs and re-uploads inside the SoundCloud results all collapse into one row; the same song in different sources stays as separate rows
- [ ] tests: CacheManager unit tests — concurrent get_or_download calls for one track start a single download, a failed download leaves no files and can be retried, eviction removes SoundCloud tracks too and keeps liked and local ones

- [ ] wave: skip-streak adaptation in db and WaveEngine — track consecutive skips in session state; on the 2nd consecutive skip, drop the current genre cluster from the candidate pool and inject one known-liked local track as a "safe" pick
- [ ] wave: mood presets (energy/focus/calm) as tempo + acoustic-feature filters on top of the ranked batch in db and WaveEngine (add track_features table migration in db.py first if missing)
- [ ] api: GET /wave/next?mood=&limit=5 returning a personalized batch from WaveEngine, session-scoped so repeated calls don't repeat already-served candidates; keep all existing routes in main.py intact

- [ ] db: playlists(id, name, created_at) and playlist_tracks(playlist_id, track_id, position) tables in db.py, position as an integer index, unique(playlist_id, track_id)
- [ ] api: POST /playlists {name} and GET /playlists listing the user's playlists with track_count from db; tracks may be local or remote (track_id is source-agnostic)
- [ ] api: POST /playlists/{id}/tracks {track_id} appends at the end (position = max+1) in db; DELETE /playlists/{id}/tracks/{track_id} removes the entry and re-indexes remaining positions
- [ ] api: GET /tracks/{id}/download streaming the file with a Content-Disposition: attachment header (local tracks or cached remote tracks only; uncached remote tracks return 404 with a clear message)
- [ ] frontend: playlists view — add topbar nav item 05 «Плейлисты» listing the playlists as an index list (number, name, track count); "+ Новый плейлист" opens a modal (name input, create button, calls POST /playlists)
- [ ] frontend: context menu on track cards with "Добавить в плейлист" (submenu of existing playlists) and "Скачать на диск" (triggers download link for the track)

- [ ] frontend: navigator.mediaSession integration — set metadata (title/artist/artwork) and action handlers (play/pause/previoustrack/nexttrack) so OS lock-screen and hardware media-key controls work
- [ ] frontend: global keyboard shortcuts — Space (play/pause, ignored while focus is in an input), ArrowLeft/ArrowRight (seek ±5s), L (like current track), N (next track)
- [ ] frontend: mobile layout on top of the existing media queries (topbar nav scrolls horizontally, nav numbers hidden below 600px) — the floating player dock becomes a swipeable bottom sheet (collapsed mini-bar by default, swipe up for the full view with the waveform scrubber)
- [ ] frontend: infinite scroll / "load more" pagination on the main tracklist instead of loading everything at once
- [ ] frontend: PWA manifest.json (name, icons, theme_color) + a minimal service worker storing the app shell offline, so the player is installable on a mobile home screen

- [ ] db: auth users(id, username, password_hash) table in db.py with password hashing on registration; plaintext password is never stored or logged
- [ ] api: auth endpoints POST /auth/register, POST /auth/login, GET /auth/me with signed session tokens and FastAPI Depends() helper; keep existing /tracks, /favorites and /history routes working for anonymous/guest requests so existing api tests pass
- [ ] frontend: login modal (username/password form) + "Продолжить как гость" fallback allowing local playback while hiding personal actions until logged in

- [ ] api: global exception handler in main.py returning structured JSON errors ({"error": "..."}) instead of raw tracebacks, while preserving standard FastAPI HTTPException status codes
- [ ] api: update GET /health in main.py to return {"status": "ok", "db": True} after verifying a simple SELECT 1 on SQLite db
- [ ] providers: wrap yt-dlp and ytmusicapi calls with a timeout + retry (max 2 attempts, exponential backoff) and a graceful empty-result fallback on failure, logged as a warning rather than raised
- [ ] db: nightly SQLite backup helper script backup_db.py (copies tracks.db with a timestamp suffix into backups/, keeps the last 7)
- [ ] api: pydantic Settings configuration module loading LIBRARY_PATH, CACHE_DIR, CACHE_MAX_SIZE_GB from .env with safe defaults, replacing hardcoded paths in providers/cache modules
- [ ] api: ENABLE_REMOTE_SOURCE config flag — when false in env/settings, remote search and remote stream endpoints return 403 so the backend can run in local-library-only mode

- [ ] tests: integration test in test_api.py for POST /tracks/remote — mocks RemoteDiscoveryProvider, asserts the track is persisted in db with source='remote' and external_id, and is retrievable via GET /tracks
- [ ] tests: unit tests for WaveEngine scoring and db history exclusions — verify the 60/40 mix ratio, the skip-streak genre-shift logic, and the recently-played exclusion filter
- [ ] ci: GitHub Actions workflow (.github/workflows/tests.yml) running `pytest -q` on every push to main
- [ ] docker: Dockerfile (python:3.12-slim base, ffmpeg via apt, copies the app, runs uvicorn) and docker-compose.yml wiring persistent volumes for tracks.db and cache/
- [ ] docs: README.md with an architecture diagram, setup/quickstart instructions and feature overview

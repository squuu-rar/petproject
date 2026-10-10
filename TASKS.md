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
- [x] frontend: search bar with 300ms debounce calling GET /search?q=; replaces the tracklist view with results grouped by source ("Локально" / "Найдено"), with empty-state and loading-state
- [x] frontend: player dock shows album artwork (cover_path or placeholder), elapsed/total time formatted mm:ss, progress bar synced to `audio.currentTime` via the `timeupdate` event, draggable to seek
- [x] frontend: playback queue module (array + index pointer) exposing next()/previous()/playAt(i); auto-advances to the next queued track on the `ended` event, no-ops at the end of the queue
- [x] frontend: heart/like button in the player dock calling POST /tracks/{id}/like, toggling filled/outline state optimistically and reconciling with the API response
- [x] api: GET /favorites — tracks WHERE liked = 1 regardless of source, same response shape as GET /tracks
- [x] frontend: "Любимые треки" section in the sidebar rendering GET /favorites results with the same track card component as the main list

- [x] cache: background task (FastAPI BackgroundTasks) triggered on the first remote stream request — downloads the track via yt-dlp into the cache dir, updates tracks.cache_path when done; subsequent requests for the same track_id are served from disk instead of redirecting again
- [x] history: POST /history {track_id, event: play|skip|finish}; "skip" is only accepted if elapsed_seconds < 15, otherwise rejected with 400
- [x] history: GET /history?limit=80 — most recent play/finish events joined with track metadata, ordered by timestamp desc

- [x] wave: WaveEngine.generate_candidates(seed_track_ids) using ytmusicapi's get_watch_playlist radio per seed track, deduped, returned with source metadata attached
- [x] wave: integrate Last.fm collaborative filtering into recommendation candidates
- [x] search: SoundCloud search and audio stream extraction via yt-dlp
- [x] frontend: "Моя Волна" single-track hero view with infinite stream prefetching
- [x] wave: candidate filter excluding track_ids played within the last N days OR skipped 2+ times, applied before scoring
- [x] wave: hybrid scorer mixing 60% remote radio candidates / 40% locally-liked library tracks into one ranked batch, mix ratio as a named constant
- [ ] api: GET /artists/{name} — local data only: {name, avatar, track_count, tracks[]}; match the artist case-insensitively in Python (SQLite LOWER() breaks on Cyrillic), tracks sorted by play count from the history table (events play/finish), then liked, then id, same shape as GET /tracks; avatar = cover_path of the most played track, else the first track with a cover; 404 when the artist has no tracks
- [ ] api: enrich GET /artists/{name} with a remote avatar — Last.fm artist.getInfo image if the Last.fm integration is configured, else the first ytmusic search(filter='artists') thumbnail, else the local cover; 4s timeout per lookup, in-memory cache of the result, any failure falls back to the local avatar without raising
- [ ] frontend: artist profile view in the app's own hi-fi style (do NOT copy Yandex Music): large round avatar, artist name in the display font (Unbounded), mono caption "N ТРЕКОВ", a vermilion «Слушать» key that plays the artist's tracks as a queue via playFromList, and the popular tracks as the standard track table (createTrackRow); no new nav item — the view opens on top of the current section
- [ ] frontend: make the artist name clickable everywhere (track rows, search results, player dock, wave deck) and open the artist view; call e.stopPropagation() in track rows so the click does not start playback; keep keyboard access (the name is a button or link with focus-visible)
- [ ] frontend: back navigation for the artist view — hash route #/artist/<name>, a «← Назад» ghost button, browser back/forward support, and restoring the previous section (library, favorites or search query) when returning

- [ ] frontend: loading state for slow first plays — spinner on the play keys (dock and wave deck) while audio fires waiting/loadstart, removed on playing/canplay; on an audio error retry the same track once after 3 seconds before auto-skipping, so a track that is still being cached is not skipped
- [ ] cache: prefetch the next 2 tracks of the queue in the background — POST /cache/prefetch {track_ids} calls CacheManager.get_or_download without waiting, the frontend sends it when a track starts playing; skip local tracks and tracks that are already cached
- [ ] tests: SearchOrchestrator deduplication — a SoundCloud track already saved in the DB (also with source='remote'), repeated URLs and re-uploads inside the SoundCloud results all collapse into one row; the same song in different sources stays as separate rows
- [ ] tests: CacheManager — concurrent get_or_download calls for one track start a single download, a failed download leaves no files and can be retried, eviction removes SoundCloud tracks too and keeps liked and local ones

- [ ] wave: skip-streak adaptation — track consecutive skips in session state; on the 2nd consecutive skip, drop the current genre cluster from the candidate pool and inject one known-liked local track as a "safe" pick
- [ ] wave: mood presets (energy/focus/calm) as tempo + acoustic-feature filters on top of the ranked batch (needs a track_features table — add that migration first if it's missing)
- [ ] api: GET /wave/next?mood=&limit=5 returning a personalized batch from WaveEngine, session-scoped so repeated calls don't repeat already-served candidates

- [x] lyrics: LRCLIB API integration in lyrics.py to fetch synchronized LRC lyrics (syncedLyrics) by artist, title and duration with fallback to plain lyrics
- [ ] frontend: synced karaoke lyrics overlay (data from GET /tracks/{id}/lyrics) with live active-line tracking via audio.timeupdate, smooth center-scroll and line-click seek; open it from a lyrics key in the player dock and from the secondary key row of the wave deck (next to the copy key); style it in the app's own hi-fi look: bone text on graphite, the active line in the vermilion accent, mono timestamps

- [ ] db: playlists(id, name, created_at) and playlist_tracks(playlist_id, track_id, position) tables, position as an integer index, unique(playlist_id, track_id)
- [ ] playlists: POST /playlists {name} and GET /playlists listing the user's playlists with track_count; tracks may be local or remote (track_id is source-agnostic)
- [ ] playlists: POST /playlists/{id}/tracks {track_id} appends at the end (position = max+1); DELETE /playlists/{id}/tracks/{track_id} removes the entry and re-indexes remaining positions
- [ ] api: GET /tracks/{id}/download streaming the file with a Content-Disposition: attachment header (local tracks only; remote, not-yet-cached tracks return 404 with a clear message)
- [ ] frontend: playlists view — the app now has a top navigation bar instead of a sidebar, so add nav item 05 «Плейлисты» listing the playlists as an index list (number, name, track count); "+ Новый плейлист" opens a modal (name input, create button, calls POST /playlists)
- [ ] frontend: context menu on track cards with "Добавить в плейлист" (submenu of existing playlists) and "Скачать на диск" (calls the download endpoint above)

- [ ] frontend: MediaSession API — set metadata (title/artist/artwork) and action handlers (play/pause/previoustrack/nexttrack) so OS lock-screen/media-key controls work and playback continues with the screen locked
- [ ] frontend: global keyboard shortcuts — Space (play/pause, ignored while focus is in an input), ArrowLeft/ArrowRight (seek ±5s), L (like current track), N (next track)
- [ ] frontend: mobile layout on top of the existing media queries (topbar nav scrolls horizontally, nav numbers hidden below 600px) — the floating player dock becomes a swipeable bottom sheet (collapsed mini-bar by default, swipe up for the full view with the waveform scrubber); no sidebar drawer is needed anymore
- [x] frontend: toast notification component for API errors (failed search, failed like, network error) — non-blocking, auto-dismisses after ~4s
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
- [x] frontend: fix track-item layout grid and spacing between duration and like button
- [x] backend: save remote track duration on search and registration so it shows immediately
- [x] backend: stream and cache remote audio files locally to disk for instant replay

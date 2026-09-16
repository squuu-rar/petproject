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
- [ ] providers: implement remote stream extraction using yt-dlp to get direct audio URLs

- [ ] api: GET /tracks with pagination, sorting and filter by source (local/remote)
- [ ] api: GET /search?q= querying both local database and remote discovery provider
- [ ] api: unified GET /stream/{track_id} supporting both local files and remote streaming
- [ ] api: support HTTP 206 Range headers for seamless audio seeking on streams
- [ ] cache: implement LRU disk cache manager for remote tracks (auto-evict oldest unliked files)
- [ ] cache: background worker caching streamed chunks to local disk during playback

- [ ] frontend: HTML5 responsive player layout (sidebar, tracklist view, bottom player dock)
- [ ] frontend: CSS styles with dark modern palette, smooth hover states and track cards
- [ ] frontend: unified audio player engine supporting stream buffering, play, pause, seek
- [ ] frontend: volume slider with muted toggle and localStorage persistence
- [ ] frontend: search bar with debounced requests to unified /search endpoint
- [ ] frontend: display album artwork, track duration and elapsed time in player dock
- [ ] frontend: active playback queue data structure (next, previous, auto-play next track)

- [ ] history: POST /history recording play, skip (under 15s) and finish events
- [ ] favorites: POST /tracks/{id}/like toggling favorite status and marking for permanent disk retention
- [ ] api: GET /favorites returning all liked tracks regardless of local or remote origin
- [ ] frontend: heart icon button in player dock with reactive state update
- [ ] frontend: "Любимые треки" tab in sidebar rendering favorites collection

- [ ] wave: create WaveEngine candidate generator using ytmusicapi get_watch_playlist radio
- [ ] wave: filter out tracks recently played or skipped in user history
- [ ] wave: hybrid mixing algorithm (60% remote recommendations, 40% local library favorites)
- [ ] wave: real-time adaptation (if 2 skips in a row, shift genre cluster and inject safe liked track)
- [ ] api: GET /wave/next returning dynamic batch of 5 personalized tracks
- [ ] frontend: "Моя Волна" master button with animated waveform pulse during playback
- [ ] wave: mood / vibe filter modes (energy, focus, calm) based on tempo and acoustic presets
- [ ] frontend: vibe chips selector (Бодрое, Спокойное, Открытия) directly modifying wave stream

- [ ] playlists: POST /playlists and GET /playlists supporting mixed local and remote tracks
- [ ] playlists: POST /playlists/{id}/tracks and DELETE /playlists/{id}/tracks/{track_id}
- [ ] frontend: playlist drawer in sidebar and modal to create new playlist
- [ ] frontend: context menu on track cards (Добавить в плейлист, Скачать на диск)

- [ ] transcode: auto-detect client bandwidth and transcode streams to Opus 128k via ffmpeg
- [ ] frontend: MediaSession API integration (OS lock screen controls, background playback, artwork)
- [ ] frontend: keyboard shortcuts (Space - play/pause, Left/Right - seek 5s, L - like, N - next)
- [ ] frontend: mobile adaptive view with swipeable bottom drawer player

- [ ] auth: user credentials table and password hashing using bcrypt
- [ ] auth: session token generation and FastAPI Depends security handler
- [ ] auth: login modal dialog on frontend and guest mode fallback

- [ ] tests: unit tests for LocalStorageProvider and RemoteDiscoveryProvider mocks
- [ ] tests: integration tests for unified /stream Range requests
- [ ] tests: unit tests for WaveEngine candidate scoring and skip penalty rules
- [ ] docker: Dockerfile with ffmpeg, Python environment and SQLite volume
- [ ] docker: docker-compose.yml ready for single-command production deployment
- [ ] docs: complete README.md with system architecture diagram, screenshots and quickstart

# Очередь задач

Каждая строка — одна задача на один день/коммит.
Формат: "- [ ] область: что сделать" (после выполнения daily_dev.py сам меняет на "[x]").

- [x] scanner: parse ID3/FLAC tags with mutagen, write to tracks table (SQLite)
- [ ] scanner: handle edge cases (missing tags fallback to filename, OGG/Opus/M4A support)
- [ ] scanner: extract embedded cover art (APIC/FLAC picture) and cache to covers directory
- [ ] scanner: calculate SHA256 file hash to prevent duplicate records when files are moved
- [ ] scanner: add incremental scan mode comparing file mtime with DB timestamp
- [ ] scanner: CLI entrypoint in scanner.py with argparse for manual folder re-indexing

- [ ] db: create indexes on tracks(artist, album, genre) and setup SQLite FTS5 virtual table
- [ ] db: add schema versioning and basic migration helper in db.py
- [ ] db: add soft-delete flag for tracks whose audio files were removed from disk
- [ ] db: add playlists_tracks table supporting custom track ordering

- [ ] api: GET /tracks with limit, offset pagination and sorting options
- [ ] api: GET /tracks/search with query string filtering across title, artist, album
- [ ] api: GET /tracks/{id} returning track details and technical metadata
- [ ] api: GET /artists and GET /artists/{name}/tracks
- [ ] api: GET /albums and GET /albums/{id}/tracks
- [ ] api: GET /stats returning library summary (total duration, track count, genre stats)
- [ ] api: GET /stream/{track_id} supporting HTTP 206 Partial Content (Range header)
- [ ] api: correct MIME type handling for mp3, flac, ogg, opus in streaming response
- [ ] api: GET /tracks/{id}/cover serving cached album art with ETag and 304 Not Modified

- [ ] frontend: HTML5 layout skeleton (sidebar, track table view, bottom player bar)
- [ ] frontend: CSS styling with dark palette and responsive grid
- [ ] frontend: audio element integration with custom play, pause and seek scrubber
- [ ] frontend: track table rendering via fetch from /tracks endpoint
- [ ] frontend: volume slider with state persistence in localStorage
- [ ] frontend: current playing track indicator, time elapsed and total duration display
- [ ] frontend: search bar input with debounced request to /tracks/search
- [ ] frontend: cover art rendering in bottom now-playing card

- [ ] auth: user credentials table in db.py and password hashing with argon2 or bcrypt
- [ ] auth: POST /auth/login returning session token and cookie
- [ ] auth: FastAPI security dependency checking session validity
- [ ] auth: POST /auth/logout invalidating active session
- [ ] frontend: login modal dialog and session persistence in browser

- [ ] playlists: POST /playlists (create) and GET /playlists (list)
- [ ] playlists: DELETE /playlists/{id} and PUT /playlists/{id} (rename)
- [ ] playlists: POST /playlists/{id}/tracks and DELETE /playlists/{id}/tracks/{track_id}
- [ ] frontend: playlist navigation in sidebar and modal to create new playlist
- [ ] frontend: context menu or action button on tracks to add into playlist
- [ ] frontend: active play queue data structure (next, previous, auto-advance on track end)

- [ ] history: POST /history endpoint recording play, skip and like events
- [ ] frontend: track listen thresholds (log 'play' after 50% played, 'skip' if skipped under 15s)
- [ ] frontend: heart icon button on player bar toggling track like state
- [ ] api: GET /tracks/favorites returning liked tracks list
- [ ] frontend: quick filter tab in sidebar for favorite tracks

- [ ] features: add librosa, soundfile and numpy to requirements.txt
- [ ] db: create track_features table (bpm, spectral_centroid, energy, mfcc_json)
- [ ] features: worker function extracting BPM and spectral contrast using librosa
- [ ] features: extract 13 MFCC coefficients and store averaged vector per track
- [ ] features: background worker indexing acoustic features for newly scanned tracks

- [ ] wave: v1 rule-based candidate generator by matching artist and genre
- [ ] wave: v1 penalty system filtering out tracks played or skipped in the last 24h
- [ ] wave: temperature parameter mixing familiar artist tracks with random discoveries
- [ ] wave: GET /wave/next returning personalized batch of 5 tracks
- [ ] frontend: "Моя Волна" mode button initiating endless queue auto-replenishment
- [ ] wave: cosine similarity function comparing MFCC feature vectors
- [ ] wave: hybrid scoring combining genre match (40%), acoustic similarity (40%) and like boost (20%)
- [ ] wave: vibe filter modes (energetic, calm, discovery) based on tempo and energy thresholds
- [ ] frontend: vibe selector chips (Бодрое, Спокойное, Незнакомое) for wave radio mode

- [ ] transcode: check ffmpeg availability and fallback to raw file stream
- [ ] transcode: on-the-fly transcoding pipeline to Opus 128k using ffmpeg subprocess
- [ ] api: GET /stream/{id}?format=opus parameter for low-bandwidth mode
- [ ] frontend: audio stream quality switcher (Original / Data Saver) in settings

- [ ] frontend: Navigator MediaSession API integration (OS lock screen controls, title, artwork)
- [ ] frontend: keyboard shortcuts (Space for play/pause, Left/Right for seek, L for like)
- [ ] frontend: mobile responsive view with collapsible bottom drawer for player
- [ ] tests: integration tests for /stream Range requests and 206 responses
- [ ] tests: unit tests for wave recommendation scoring and penalty weights
- [ ] tests: end-to-end test for user creation, playlist creation, track addition
- [ ] docker: Dockerfile and docker-compose.yml mounting music folder and SQLite database
- [ ] docs: update README.md with complete architecture diagram, API docs and setup guide

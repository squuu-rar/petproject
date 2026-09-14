# Очередь задач

Каждая строка — одна задача на один день/коммит.
Формат: "- [ ] область: что сделать" (после выполнения daily_dev.py сам меняет на "[x]").

- [ ] scanner: parse ID3/FLAC tags with mutagen, write to tracks table (SQLite)
- [ ] scanner: handle edge cases (missing tags, OGG/Opus files)
- [ ] api: GET /tracks with pagination and basic filters
- [ ] api: GET /stream/{track_id} with HTTP Range support
- [ ] frontend: minimal player page (track list, play/pause, progress bar)
- [ ] auth: simple session-based login (single-user is ok for v1)
- [ ] playlists: CRUD endpoints + minimal UI
- [ ] history: log play/skip/like events to DB
- [ ] wave: v1 recommendation by genre/artist + randomness, auto-queue
- [ ] tests: add pytest coverage for scanner and streaming endpoints

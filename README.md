# Petproject Music Player

Self-hosted музыкальный плеер с персональной волной рекомендаций.

## Архитектура
- **`main.py`**: Точка входа FastAPI (REST API + раздача static/ streaming).
- **`db.py`**: Подключение к SQLite (`tracks.db`) и инициализация таблиц.
- **`scanner.py`**: Модуль сканирования директории с музыкой и извлечения ID3/FLAC метаданных via `mutagen`.
- **`wave.py`**: Алгоритм формирования автоочередности ("Моя Волна").
- **`static/`**: Минимальный веб-интерфейс (HTML/JS) для воспроизведения.

## Схема БД (SQLite)
- `tracks`: id, path, title, artist, album, genre, track_number, duration.
- `history`: id, track_id, event_type (play/skip/like), timestamp.
- `playlists`: id, name, track_ids.

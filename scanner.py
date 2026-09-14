import pathlib
import mutagen
from db import get_db

EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
}
SUPPORTED_EXT = {".mp3", ".flac"}

def _extract_track_data(file_path: pathlib.Path):
    try:
        f = mutagen.File(file_path, easy=True)
    except Exception:
        f = None

    if f is None or f.tags is None:
        return None

    data = {}
    tags = f.tags
    for key, db_key in EASY_KEYS.items():
        if key in tags:
            val = tags[key]
            data[db_key] = val[0] if isinstance(val, list) else val

    raw_track = tags.get("tracknumber")
    if raw_track:
        val = raw_track[0] if isinstance(raw_track, list) else raw_track
        try:
            data["track_number"] = int(str(val).split("/")[0])
        except ValueError:
            data["track_number"] = None

    if hasattr(f, "info") and hasattr(f.info, "length"):
        data["duration"] = f.info.length

    return data

def _upsert_track(conn, track, path_str):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tracks (path, title, artist, album, genre, track_number, duration)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            album=excluded.album,
            genre=excluded.genre,
            track_number=excluded.track_number,
            duration=excluded.duration
        """,
        (
            path_str,
            track.get("title"),
            track.get("artist"),
            track.get("album"),
            track.get("genre"),
            track.get("track_number"),
            track.get("duration"),
        ),
    )

def scan(path: pathlib.Path):
    conn = get_db()
    try:
        for p in path.rglob("*"):
            if p.suffix.lower() in SUPPORTED_EXT:
                data = _extract_track_data(p)
                if data:
                    _upsert_track(conn, data, str(p))
        conn.commit()
    finally:
        conn.close()

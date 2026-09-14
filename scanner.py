import pathlib
import mutagen
from db import get_db, DB_PATH

# Simple mapping for easy tags
EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
    "tracknumber": "track_number",
    "duration": "duration",
}

def _extract_track_data(file_path: pathlib.Path):
    """Return dict of track metadata extracted from file_path using mutagen.
    Fields:
        path, title, artist, album, genre, track_number, duration
    """
    tags = {}
    data = {}
    try:
        f = mutagen.File(file_path, easy=True)
    except Exception:
        f = None
    if f is None or f.tags is None:
        return None
    tags = f.tags
    for key, db_key in EASY_KEYS.items():
        if key in tags:
            value = tags[key]
            if isinstance(value, list):
                value = value[0]
            data[db_key] = value
    # duration from file info length
    if hasattr(f, "info") and hasattr(f.info, "length"):
        data["duration"] = f.info.length
    return data


def _upsert_track(conn, track, path_str):
    # Use path as unique
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO tracks (path) VALUES (?)", (path_str,)
    )
    cur.execute("SELECT id FROM tracks WHERE path = ?", (path_str,))
    row = cur.fetchone()
    if row is None:
        # shouldn't happen
        return
    track_id = row[0]
    # update with rest of fields
    cur.execute(
        "UPDATE tracks SET title=?, artist=?, album=?, genre=?, track_number=?, duration=? WHERE id=?",
        (
            track.get("title"),
            track.get("artist"),
            track.get("album"),
            track.get("genre"),
            track.get("track_number"),
            track.get("duration"),
            track_id,
        ),
    )
    conn.commit()


def scan(path: pathlib.Path):
    """Scan a directory for mp3 and flac files and store metadata in DB."""
    conn = get_db()
    for p in path.rglob("*.mp3"):
        data = _extract_track_data(p)
        if data:
            _upsert_track(conn, data, str(p))
    for p in path.rglob("*.flac"):
        data = _extract_track_data(p)
        if data:
            _upsert_track(conn, data, str(p))
    conn.close()
    return

import pathlib
import mutagen
from db import get_db
import hashlib
import os

EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
}
SUPPORTED_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a"}

BASE_DIR = pathlib.Path(__file__).resolve().parent
COVERS_DIR = BASE_DIR / "static" / "covers"


def _extract_track_data(file_path: pathlib.Path):
    try:
        f = mutagen.File(file_path, easy=True)
    except Exception:
        f = None

    if f is None or not hasattr(f, "info"):
        return None

    tags = f.tags or {}
    data = {}
    for key, db_key in EASY_KEYS.items():
        if key in tags and tags[key]:
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

    if "title" not in data:
        data["title"] = file_path.stem

    return data, f


def _upsert_track(conn, track: dict, path_str: str):
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


def _extract_cover_art(file_path: pathlib.Path, f):
    picture_data = None
    # MP3 APIC frame extraction
    if hasattr(f, "tags"):
        tags = f.tags
        if isinstance(tags, dict):
            if "APIC" in tags:
                apic = tags.get("APIC")
                picture_data = getattr(apic, "data", None)
        else:
            try:
                apic_frame = f.tags.get("APIC")
                picture_data = getattr(apic_frame, "data", None)
            except Exception:
                pass

    # FLAC picture extraction
    if picture_data is None and hasattr(f, "pictures"):
        pics = getattr(f, "pictures", None)
        if pics and len(pics) > 0:
            pic = pics[0]
            picture_data = getattr(pic, "data", None)

    if picture_data:
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        hash_val = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:12]
        cover_path = COVERS_DIR / f"{hash_val}.jpg"
        with open(cover_path, "wb") as fp:
            fp.write(picture_data)
        return cover_path.as_posix()
    return None


def scan(path: pathlib.Path):
    conn = get_db()
    try:
        for p in path.rglob("*"):
            if p.suffix.lower() in SUPPORTED_EXT:
                data_f = _extract_track_data(p)
                if data_f:
                    data, f_obj = data_f
                    _upsert_track(conn, data, str(p))
                    _extract_cover_art(p, f_obj)
        conn.commit()
    finally:
        conn.close()

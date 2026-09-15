import hashlib
import os
import pathlib
import mutagen
from db import get_db

EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "date": "year",
    "tracknumber": "track_number",
}
SUPPORTED_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a"}

BASE_DIR = pathlib.Path(__file__).resolve().parent
COVERS_DIR = BASE_DIR / "static" / "covers"

def _extract_track_data(file_path: pathlib.Path):
    try:
        f = mutagen.File(file_path, easy=True)
    except Exception:
        return None

    if f is None or not getattr(f, "tags", None):
        return None

    data = {}
    for easy_k, db_k in EASY_KEYS.items():
        val = f.tags.get(easy_k)
        if val:
            data[db_k] = str(val[0])

    if "track_number" in data:
        raw_track = data["track_number"]
        try:
            data["track_number"] = int(str(raw_track).split("/")[0])
        except (ValueError, TypeError):
            data["track_number"] = None
    else:
        data["track_number"] = None

    if "year" in data:
        try:
            data["year"] = int(str(data["year"])[:4])
        except (ValueError, TypeError):
            data["year"] = None

    data["duration"] = getattr(getattr(f, "info", None), "length", None)

    if "title" not in data or not data["title"]:
        data["title"] = file_path.stem

    return data, f

def _extract_cover_art(file_path: pathlib.Path, f=None) -> str | None:
    picture_data = None

    # 1. FLAC / OGG / Opus
    if f is not None and hasattr(f, "pictures"):
        pics = getattr(f, "pictures", None)
        if pics and len(pics) > 0:
            picture_data = getattr(pics[0], "data", None)

    # 2. Обработка ID3 / словарей тегов
    if not picture_data and f is not None and hasattr(f, "tags"):
        tags = f.tags
        if isinstance(tags, dict):
            for k, v in tags.items():
                if str(k).startswith("APIC") or k == "covr":
                    picture_data = getattr(v, "data", None)
                    if picture_data is None and isinstance(v, (bytes, bytearray)):
                        picture_data = bytes(v)
                    elif picture_data is None and isinstance(v, list) and v:
                        first = v[0]
                        picture_data = getattr(first, "data", None) or (bytes(first) if isinstance(first, (bytes, bytearray)) else None)
                    if picture_data:
                        break
        if not picture_data and hasattr(tags, "getall"):
            try:
                apics = tags.getall("APIC")
                if apics:
                    picture_data = getattr(apics[0], "data", None)
            except Exception:
                pass

    # 3. Прямое чтение бинарных тегов для реальных аудиофайлов
    if not picture_data and file_path.exists() and file_path.stat().st_size > 0:
        try:
            raw_audio = mutagen.File(file_path)
            if raw_audio is not None:
                if hasattr(raw_audio, "pictures") and raw_audio.pictures:
                    picture_data = raw_audio.pictures[0].data
                elif hasattr(raw_audio, "tags") and raw_audio.tags:
                    if hasattr(raw_audio.tags, "getall"):
                        apics = raw_audio.tags.getall("APIC")
                        if apics:
                            picture_data = getattr(apics[0], "data", None)
                    elif isinstance(raw_audio.tags, dict):
                        for k, v in raw_audio.tags.items():
                            if str(k).startswith("APIC"):
                                picture_data = getattr(v, "data", None)
                                if picture_data:
                                    break
                            elif k == "covr" and v:
                                picture_data = bytes(v[0])
                                break
        except Exception:
            pass

    if not picture_data:
        return None

    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    img_hash = hashlib.sha256(picture_data).hexdigest()[:12]
    cover_path = COVERS_DIR / f"{img_hash}.jpg"
    if not cover_path.exists():
        with open(cover_path, "wb") as fp:
            fp.write(picture_data)

    return f"/static/covers/{cover_path.name}"

def _upsert_track(conn, track: dict, path_str: str):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tracks (path, title, artist, album, year, track_number, duration, cover_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            album=excluded.album,
            year=excluded.year,
            track_number=excluded.track_number,
            duration=excluded.duration,
            cover_path=excluded.cover_path
        """,
        (
            path_str,
            track.get("title"),
            track.get("artist"),
            track.get("album"),
            track.get("year"),
            track.get("track_number"),
            track.get("duration"),
            track.get("cover_path"),
        ),
    )

def scan(path: pathlib.Path):
    conn = get_db()
    try:
        for p in path.rglob("*"):
            if p.suffix.lower() in SUPPORTED_EXT:
                data_f = _extract_track_data(p)
                if data_f:
                    data, f_obj = data_f
                    cover_url = _extract_cover_art(p, f_obj)
                    data["cover_path"] = cover_url
                    _upsert_track(conn, data, str(p))
        conn.commit()
    finally:
        conn.close()

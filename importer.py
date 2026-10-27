import hashlib
import os
import re
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
except ImportError:
    mutagen = None

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}
BASE_DIR = Path(__file__).resolve().parent
COVERS_DIR = BASE_DIR / "static" / "covers"
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
COVERS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

GENERIC_FOLDER_NAMES = {
    "uploads",
    "music",
    "downloads",
    "загрузки",
    "музыка",
    "яндекс музыка",
    "yandex music",
    "yandex_export",
    "favorites",
    "любимые",
    "liked",
    "tracks",
    "mp3",
    "audio",
}


def _fix_mojibake(val: Optional[str]) -> Optional[str]:
    if not val or not isinstance(val, str):
        return None
    val = val.replace("\x00", "").strip()
    if not val:
        return None
    if any("\u0400" <= ch <= "\u04ff" for ch in val):
        return val
    if any("\u00c0" <= ch <= "\u00ff" for ch in val):
        try:
            decoded = val.encode("latin1").decode("cp1251")
            if sum(1 for ch in decoded if "\u0400" <= ch <= "\u04ff") >= 2:
                return decoded.strip()
        except Exception:
            pass
    return val


def _save_cover_bytes(data: bytes, ext: str = ".jpg") -> Optional[str]:
    if not data or len(data) < 32:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    digest = hashlib.md5(data).hexdigest()
    filename = f"{digest}{ext}"
    out_path = COVERS_DIR / filename
    if not out_path.exists():
        try:
            out_path.write_bytes(data)
        except Exception:
            return None
    return f"/static/covers/{filename}"


def _parse_filename_fallback(file_path: Path) -> tuple[str, str]:
    stem = file_path.stem.strip()
    # Убираем ведущие номера треков вроде "01. " или "001 - "
    stem = re.sub(r"^(?:\[\d{1,4}\]|\d{1,4})[\s.\-_)]+", "", stem).strip()
    stem = re.sub(r"\s*(?:\[\d{2,3}\s*kbps\]|\(\d{2,3}\s*kbps\))$", "", stem, flags=re.IGNORECASE).strip()
    for sep in (" — ", " – ", " - ", " _ ", "—", "–", "-", "_"):
        if sep in stem:
            parts = stem.split(sep, 1)
            artist, title = parts[0].strip(), parts[1].strip()
            if artist and title and not artist.isdigit():
                return (_fix_mojibake(artist) or artist), (_fix_mojibake(title) or title)

    parent_name = file_path.parent.name.strip()
    if parent_name and parent_name.lower() not in GENERIC_FOLDER_NAMES and not parent_name.startswith(".") and not parent_name.startswith("test_"):
        return parent_name, (stem or file_path.name)

    return "Неизвестный исполнитель", (stem or file_path.name)


def extract_audio_metadata(file_path: Path) -> Dict[str, Any]:
    file_path = Path(file_path)
    fallback_artist, fallback_title = _parse_filename_fallback(file_path)

    meta: Dict[str, Any] = {
        "path": str(file_path.resolve()),
        "title": fallback_title,
        "artist": fallback_artist,
        "album": file_path.parent.name if file_path.parent.name.lower() not in GENERIC_FOLDER_NAMES else "-",
        "genre": None,
        "year": None,
        "track_number": None,
        "duration": None,
        "cover_path": None,
        "source": "local",
    }

    if mutagen is not None:
        try:
            audio = mutagen.File(str(file_path), easy=True)
            if audio is not None:
                if getattr(audio, "info", None) and getattr(audio.info, "length", None):
                    meta["duration"] = round(float(audio.info.length), 2)

                tags = audio.tags or {}

                def _first(key: str) -> Optional[str]:
                    val = tags.get(key)
                    if isinstance(val, list) and val:
                        return _fix_mojibake(str(val[0]).strip())
                    if isinstance(val, str):
                        return _fix_mojibake(val.strip())
                    return None

                title = _first("title")
                artist = _first("artist") or _first("albumartist")
                album = _first("album")
                genre = _first("genre")
                date_str = _first("date") or _first("originaldate") or _first("year")
                tr_num = _first("tracknumber")

                if title:
                    meta["title"] = title
                if artist:
                    meta["artist"] = artist
                if album:
                    meta["album"] = album
                if genre:
                    meta["genre"] = genre
                if date_str:
                    m = re.search(r"(\d{4})", date_str)
                    if m:
                        meta["year"] = int(m.group(1))
                if tr_num:
                    m = re.match(r"(\d+)", tr_num)
                    if m:
                        meta["track_number"] = int(m.group(1))

            # Извлекаем встроенную обложку
            raw = mutagen.File(str(file_path))
            if raw is not None and raw.tags is not None:
                for k in raw.tags.keys():
                    if str(k).startswith("APIC"):
                        apic = raw.tags[k]
                        ext = ".png" if "png" in getattr(apic, "mime", "").lower() else ".jpg"
                        meta["cover_path"] = _save_cover_bytes(apic.data, ext)
                        break
                if not meta["cover_path"] and isinstance(raw, FLAC) and raw.pictures:
                    pic = raw.pictures[0]
                    ext = ".png" if "png" in (pic.mime or "").lower() else ".jpg"
                    meta["cover_path"] = _save_cover_bytes(pic.data, ext)
                if not meta["cover_path"] and isinstance(raw, MP4):
                    covr = raw.tags.get("covr")
                    if covr:
                        data = bytes(covr[0])
                        meta["cover_path"] = _save_cover_bytes(data, ".jpg")
        except Exception:
            pass

    if not meta["cover_path"]:
        for cname in ("cover.jpg", "cover.png", "folder.jpg", "front.jpg"):
            cfile = file_path.parent / cname
            if cfile.exists():
                try:
                    meta["cover_path"] = _save_cover_bytes(cfile.read_bytes(), cfile.suffix)
                    break
                except Exception:
                    pass

    return meta


def scan_directory_for_audio(folder_path: str) -> List[Dict[str, Any]]:
    target = Path(os.path.expanduser(str(folder_path))).resolve()
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {folder_path}")

    results: List[Dict[str, Any]] = []
    for root_dir, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = Path(root_dir) / fname
            if fpath.suffix.lower() in AUDIO_EXTENSIONS:
                results.append(extract_audio_metadata(fpath))
    return results

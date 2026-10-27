import pathlib
import pytest
import scanner
import db

class DummyPic:
    def __init__(self, data):
        self.data = data

class DummyTag:
    def __init__(self, data):
        self.data = data

class DummyAudio:
    def __init__(self, tags=None, pictures=None, length=180.0):
        self.tags = tags or {}
        self.pictures = pictures or []
        self.info = type("Info", (), {"length": length})()

def test_extract_cover_flac(temp_db, temp_dir, monkeypatch):
    flac_path = temp_dir / "track.flac"
    flac_path.write_bytes(b"dummy")

    dummy_img = b"\x89PNG\r\n\x1a\n\x00\x00_flac_cover"
    tags = {"title": ["Test FLAC"], "artist": ["Artist"]}
    dummy_audio = DummyAudio(tags=tags, pictures=[DummyPic(dummy_img)])

    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: dummy_audio)
    scanner.scan(temp_dir)

    conn = db.get_db()
    cur = conn.execute("SELECT title, cover_path FROM tracks WHERE path=?", (str(flac_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["cover_path"] is not None
    assert row["cover_path"].startswith("/static/covers/")

    saved_file = scanner.BASE_DIR / row["cover_path"].lstrip("/")
    assert saved_file.exists()
    assert saved_file.read_bytes() == dummy_img

def test_extract_cover_apic_mp3(temp_db, temp_dir, monkeypatch):
    mp3_path = temp_dir / "song.mp3"
    mp3_path.write_bytes(b"dummy")

    dummy_img = b"\xff\xd8\xff_mp3_cover"
    tags = {
        "title": ["Test MP3"],
        "artist": ["Artist"],
        "APIC:": DummyTag(dummy_img),
    }
    dummy_audio = DummyAudio(tags=tags)

    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: dummy_audio)
    scanner.scan(temp_dir)

    conn = db.get_db()
    cur = conn.execute("SELECT cover_path FROM tracks WHERE path=?", (str(mp3_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["cover_path"] is not None

    saved_file = scanner.BASE_DIR / row["cover_path"].lstrip("/")
    assert saved_file.exists()
    assert saved_file.read_bytes() == dummy_img

def test_cover_art_deduplication(temp_db, temp_dir, monkeypatch):
    song1 = temp_dir / "s1.mp3"
    song2 = temp_dir / "s2.mp3"
    song1.write_bytes(b"dummy1")
    song2.write_bytes(b"dummy2")

    same_img = b"identical_cover_binary_data"
    tags = {"title": ["Song"], "APIC": DummyTag(same_img)}
    dummy_audio = DummyAudio(tags=tags)

    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: dummy_audio)
    scanner.scan(temp_dir)

    conn = db.get_db()
    cur = conn.execute("SELECT cover_path FROM tracks")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]["cover_path"] == rows[1]["cover_path"]

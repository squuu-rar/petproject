import pathlib
import pytest

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "tracks.db"
    import db
    original_path = db.DB_PATH
    db.DB_PATH = db_path
    try:
        db.init_db()
        yield db_path
    finally:
        db.DB_PATH = original_path

@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path

def dummy_mutagen_file(tags, length=300.0):
    class DummyInfo:
        def __init__(self, length):
            self.length = length
    class DummyFile:
        def __init__(self, tags, length):
            self.tags = tags
            self.info = DummyInfo(length)
    return DummyFile(tags, length)

def test_scan_inserts_track_missing_tags_fallback(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "song.mp3"
    file_path.write_bytes(b"")
    tags = {}  # No tags at all
    import scanner
    monkeypatch.setattr(scanner.mutagen, "File",
                      lambda name, easy=True: dummy_mutagen_file(tags, 180.0))
    scanner.scan(temp_dir)

    import db
    conn = db.get_db()
    cur = conn.execute(
        "SELECT title, artist, album, genre, track_number, duration FROM tracks WHERE path=?",
        (str(file_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    title, artist, album, genre, track_number, duration = row
    assert title == file_path.stem  # fallback to filename
    assert artist is None
    assert album is None
    assert genre is None
    assert track_number is None
    assert abs(duration - 180.0) < 1e-5

def test_scan_inserts_ogg_file(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "track.ogg"
    file_path.write_bytes(b"")
    tags = {
        "title": ["OGG Title"],
        "artist": ["OGG Artist"],
    }
    import scanner
    monkeypatch.setattr(scanner.mutagen, "File",
                      lambda name, easy=True: dummy_mutagen_file(tags, 200.0))
    scanner.scan(temp_dir)

    import db
    conn = db.get_db()
    cur = conn.execute(
        "SELECT title, artist, album, genre, track_number, duration FROM tracks WHERE path=?",
        (str(file_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    title, artist, album, genre, track_number, duration = row
    assert title == "OGG Title"
    assert artist == "OGG Artist"
    assert album is None
    assert genre is None
    assert track_number is None
    assert abs(duration - 200.0) < 1e-5

def test_scan_skips_non_supported_extension(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "song.txt"
    file_path.write_text("hello")
    import scanner
    # Even if mutagen.File returns dummy data, the file should be ignored due to unsupported extension
    monkeypatch.setattr(scanner.mutagen, "File",
                      lambda name, easy=True: dummy_mutagen_file(
                          {"title": ["Ignored"]}, 120.0))
    scanner.scan(temp_dir)

    import db
    conn = db.get_db()
    cur = conn.execute("SELECT * FROM tracks WHERE path=?", (str(file_path),))
    row = cur.fetchone()
    conn.close()
    assert row is None

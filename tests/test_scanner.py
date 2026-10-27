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

def test_scan_inserts_track(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "song.mp3"
    file_path.write_bytes(b"")
    tags = {
        "title": ["Test Title"],
        "artist": ["Test Artist"],
        "album": ["Test Album"],
        "genre": ["Test Genre"],
        "tracknumber": ["1/12"],
    }
    import scanner
    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: dummy_mutagen_file(tags, 180.0))
    scanner.scan(temp_dir)

    import db
    conn = db.get_db()
    cur = conn.execute("SELECT title, artist, album, genre, track_number, duration FROM tracks WHERE path=?", (str(file_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    title, artist, album, genre, track_number, duration = row
    assert title == "Test Title"
    assert artist == "Test Artist"
    assert album == "Test Album"
    assert genre == "Test Genre"
    assert track_number == 1
    assert abs(duration - 180.0) < 1e-5

def test_scan_skips_non_tag(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "notatag.txt"
    file_path.write_text("hello")
    import scanner
    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: None)
    scanner.scan(temp_dir)

    import db
    conn = db.get_db()
    cur = conn.execute("SELECT * FROM tracks WHERE path=?", (str(file_path),))
    row = cur.fetchone()
    conn.close()
    assert row is None

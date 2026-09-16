import pytest
import db
import scanner

def test_db_schema_columns(temp_db):
    conn = db.get_db()
    cur = conn.execute("PRAGMA table_info(tracks)")
    cols = {row["name"] for row in cur.fetchall()}
    conn.close()
    assert {"source", "external_id", "cache_path"}.issubset(cols)

def test_default_source_set_on_scan(temp_db, temp_dir, monkeypatch):
    file_path = temp_dir / "song.mp3"
    file_path.write_bytes(b"dummy")

    class DummyInfo:
        def __init__(self, length):
            self.length = length

    class DummyFile:
        def __init__(self, tags, length):
            self.tags = tags
            self.info = DummyInfo(length)

    tags = {"title": ["Test"], "artist": ["Me"]}
    dummy_audio = DummyFile(tags, 180.0)

    monkeypatch.setattr(scanner.mutagen, "File", lambda name, easy=True: dummy_audio)
    scanner.scan(temp_dir)

    conn = db.get_db()
    cur = conn.execute("SELECT source, external_id, cache_path FROM tracks WHERE path=?", (str(file_path),))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["source"] == "local"
    assert row["external_id"] is None
    assert row["cache_path"] is None

import asyncio
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import db
from cache_manager import CacheManager


@pytest.fixture
def temp_cache_dir(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_cache.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    yield test_db


def test_cache_manager_download_and_evict(temp_cache_dir, temp_db):
    manager = CacheManager(temp_cache_dir, 1024 * 1024)

    conn = db.get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tracks (path, title, source, external_id, liked, last_accessed) VALUES (?, ?, ?, ?, ?, ?)",
        ("p1", "T1", "remote", "vid1", 0, 100.0),
    )
    cur.execute(
        "INSERT INTO tracks (path, title, source, external_id, liked, last_accessed) VALUES (?, ?, ?, ?, ?, ?)",
        ("p2", "T2", "remote", "vid2", 0, 200.0),
    )
    conn.commit()
    conn.close()

    with patch("cache_manager.yt_dlp.YoutubeDL") as mock_ydl_cls:
        def fake_ydl(ydl_opts):
            class DummyYDL:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def _write_dummy(self):
                    raw_tmpl = str(ydl_opts["outtmpl"])
                    real_path = Path(raw_tmpl.replace("%(ext)s", "mp3"))
                    real_path.parent.mkdir(parents=True, exist_ok=True)
                    real_path.write_bytes(b"0" * (600 * 1024))
                    return real_path

                def download(self, urls):
                    self._write_dummy()

                def extract_info(self, url, download=True):
                    p = self._write_dummy()
                    return {
                        "id": "vid1",
                        "ext": "mp3",
                        "requested_downloads": [{"filepath": str(p)}],
                    }

            return DummyYDL()

        mock_ydl_cls.side_effect = fake_ydl

        path1 = asyncio.run(manager.get_or_download(1, "url1"))
        assert path1 is not None and path1.exists()
        time.sleep(0.02)

        path2 = asyncio.run(manager.get_or_download(2, "url2"))
        assert path2 is not None and path2.exists()

        conn = db.get_db()
        row1 = conn.execute("SELECT cache_path FROM tracks WHERE id = 1").fetchone()
        row2 = conn.execute("SELECT cache_path FROM tracks WHERE id = 2").fetchone()
        conn.close()

        assert row1["cache_path"] is None
        assert row2["cache_path"] is not None

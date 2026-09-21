import pytest
import time
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from cache_manager import CacheManager
import db

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
    # Setup: 1MB limit
    manager = CacheManager(temp_cache_dir, 1024 * 1024)
    
    conn = db.get_db()
    cur = conn.cursor()
    # Create two remote tracks
    cur.execute("INSERT INTO tracks (path, title, source, external_id, liked, last_accessed) VALUES (?, ?, ?, ?, ?, ?)", 
                ("p1", "T1", "remote", "vid1", 0, 100.0))
    cur.execute("INSERT INTO tracks (path, title, source, external_id, liked, last_accessed) VALUES (?, ?, ?, ?, ?, ?)", 
                ("p2", "T2", "remote", "vid2", 0, 200.0))
    conn.commit()
    conn.close()

    # Mock yt-dlp to return a "file"
    with patch("cache_manager.yt_dlp.YoutubeDL") as mock_ydl_cls:
        def fake_ydl(ydl_opts):
            class DummyYDL:
                def download(self, urls):
                    out_path = Path(ydl_opts['outtmpl'])
                    out_path.write_bytes(b"0" * (600 * 1024)) # 600KB
            return DummyYDL()
        mock_ydl_cls.side_effect = fake_ydl
        
        # Download first track (600KB)
        path1 = asyncio.run(manager.get_or_download(1, "url1"))
        assert path1.exists()
        time.sleep(0.02)
        
        # Download second track (600KB) -> Total 1.2MB > 1MB limit
        # Should trigger eviction of track 1 (oldest unliked)
        path2 = asyncio.run(manager.get_or_download(2, "url2"))
        assert path2.exists()
        
        # Check eviction
        conn = db.get_db()
        row1 = conn.execute("SELECT cache_path FROM tracks WHERE id = 1").fetchone()
        row2 = conn.execute("SELECT cache_path FROM tracks WHERE id = 2").fetchone()
        conn.close()
        
        assert row1["cache_path"] is None
        assert row2["cache_path"] is not None

import asyncio

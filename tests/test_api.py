import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from main import app
import db
from pathlib import Path

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Sets up a clean temporary database for every test."""
    test_db = tmp_path / "test_tracks.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    
    # Create some dummy data for testing
    conn = db.get_db()
    cur = conn.cursor()
    tracks = [
        (1, "path1", "Song 1", "Artist A", "Album A", "Rock", 2020, 1, 180.0, None, "local", None, None),
        (2, "path2", "Song 2", "Artist B", "Album B", "Pop", 2021, 2, 200.0, None, "local", None, None),
        (3, "path3", "Song 3", "Artist C", "Album C", "Jazz", 2022, 3, 210.0, None, "remote", "vid1", None),
        (4, "path4", "Song 4", "Artist D", "Album D", "Rock", 2023, 4, 220.0, None, "local", None, None),
    ]
    for t in tracks:
        cur.execute("""
            INSERT OR IGNORE INTO tracks 
            (id, path, title, artist, album, genre, year, track_number, duration, cover_path, source, external_id, cache_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, t)
    conn.commit()
    conn.close()
    return test_db

def test_get_tracks_pagination(setup_test_db):
    response = client.get("/tracks?limit=2&offset=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["title"] == "Song 2"
    assert data[1]["title"] == "Song 3"

def test_get_tracks_sorting(setup_test_db):
    response = client.get("/tracks?sort_by=year&order=desc")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["year"] == 2023
    assert data[1]["year"] == 2022

def test_get_tracks_filter_source(setup_test_db):
    response = client.get("/tracks?source=local")
    assert response.status_code == 200
    data = response.json()
    assert all(t["source"] == "local" for t in data)
    assert len(data) == 3

    response = client.get("/tracks?source=remote")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["source"] == "remote"

def test_get_tracks_invalid_params(setup_test_db):
    response = client.get("/tracks?order=invalid")
    assert response.status_code == 422
    response = client.get("/tracks?source=unknown")
    assert response.status_code == 422

def test_get_tracks_empty_results(setup_test_db):
    response = client.get("/tracks?offset=100")
    assert response.status_code == 200
    assert response.json() == []

def test_get_stream_local_success(setup_test_db, tmp_path):
    # Create a real dummy file
    dummy_file = tmp_path / "test_audio.mp3"
    dummy_file.write_text("dummy content")
    
    # Update DB to point to this file
    conn = db.get_db()
    conn.execute("UPDATE tracks SET path = ? WHERE id = 1", (str(dummy_file),))
    conn.commit()
    conn.close()

    response = client.get("/stream/1")
    assert response.status_code == 200
    # Check if it's a file response (content-type for text/plain is fine for dummy)
    assert response.headers["content-type"] != "application/json"

def test_get_stream_local_not_found(setup_test_db):
    # File path in DB exists but file is missing on disk
    conn = db.get_db()
    conn.execute("UPDATE tracks SET path = '/non/existent/path.mp3' WHERE id = 1")
    conn.commit()
    conn.close()

    response = client.get("/stream/1")
    assert response.status_code == 404
    assert response.json()["detail"] == "Audio file not found on disk"

def test_get_stream_remote_redirect(setup_test_db):
    with patch("providers.YoutubeStreamProvider.get_stream_url") as mock_url:
        mock_url.return_value = "https://youtube.com/redirect_me"
        response = client.get("/stream/3", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "https://youtube.com/redirect_me"

def test_get_stream_not_found(setup_test_db):
    response = client.get("/stream/999")
    assert response.status_code == 404

def test_get_stream_remote_error(setup_test_db):
    with patch("providers.YoutubeStreamProvider.get_stream_url") as mock_url:
        mock_url.side_effect = RuntimeError("yt-dlp failed")
        response = client.get("/stream/3", follow_redirects=False)
        assert response.status_code == 500
        assert "yt-dlp failed" in response.json()["detail"]

def test_get_stream_remote_missing_id(setup_test_db):
    # Update DB to remove external_id
    conn = db.get_db()
    conn.execute("UPDATE tracks SET external_id = NULL WHERE id = 3")
    conn.commit()
    conn.close()
    
    response = client.get("/stream/3", follow_redirects=False)
    assert response.status_code == 500
    assert "missing external_id" in response.json()["detail"]

def test_get_stream_unsupported_source(setup_test_db):
    conn = db.get_db()
    conn.execute("UPDATE tracks SET source = 'unknown' WHERE id = 1")
    conn.commit()
    conn.close()
    
    response = client.get("/stream/1")
    assert response.status_code == 400
    assert "Unsupported source" in response.json()["detail"]

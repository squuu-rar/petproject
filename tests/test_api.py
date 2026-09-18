import pytest
import db
import scanner
from fastapi.testclient import TestClient
from main import app
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
        ("path1", "Song 1", "Artist A", "Album A", "Rock", 2020, 1, 180.0, None, "local", None, None),
        ("path2", "Song 2", "Artist B", "Album B", "Pop", 2021, 2, 200.0, None, "local", None, None),
        ("path3", "Song 3", "Artist C", "Album C", "Jazz", 2022, 3, 210.0, None, "remote", "vid1", None),
        ("path4", "Song 4", "Artist D", "Album D", "Rock", 2023, 4, 220.0, None, "local", None, None),
    ]
    for t in tracks:
        cur.execute("""
            INSERT OR IGNORE INTO tracks 
            (path, title, artist, album, genre, year, track_number, duration, cover_path, source, external_id, cache_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, t)
    conn.commit()
    conn.close()
    yield test_db

def test_get_tracks_pagination(setup_test_db):
    # Test limit and offset
    response = client.get("/tracks?limit=2&offset=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["title"] == "Song 2"
    assert data[1]["title"] == "Song 3"

def test_get_tracks_sorting(setup_test_db):
    # Test sorting by year DESC
    response = client.get("/tracks?sort_by=year&order=desc")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["year"] == 2023
    assert data[1]["year"] == 2022

def test_get_tracks_filter_source(setup_test_db):
    # Test filter local
    response = client.get("/tracks?source=local")
    assert response.status_code == 200
    data = response.json()
    assert all(t["source"] == "local" for t in data)
    assert len(data) == 3

    # Test filter remote
    response = client.get("/tracks?source=remote")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["source"] == "remote"

def test_get_tracks_invalid_params(setup_test_db):
    # Test invalid order
    response = client.get("/tracks?order=invalid")
    assert response.status_code == 422

    # Test invalid source
    response = client.get("/tracks?source=unknown")
    assert response.status_code == 422

    # Test invalid sort_by (should fallback to 'id' per implementation)
    response = client.get("/tracks?sort_by=non_existent_column")
    assert response.status_code == 200
    assert len(response.json()) == 4

def test_get_tracks_empty_results(setup_test_db):
    # Test offset beyond range
    response = client.get("/tracks?offset=100")
    assert response.status_code == 200
    assert response.json() == []

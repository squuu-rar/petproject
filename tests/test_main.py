from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


def test_cache_prefetch(monkeypatch):
    import main
    scheduled_ids = []

    async def fake_get_or_download(tid: int):
        scheduled_ids.append(tid)

    def fake_get_track(tid: int):
        mapping = {
            1: {"id": 1, "source": "local", "cache_path": None},
            2: {"id": 2, "source": "remote", "cache_path": "/tmp/already.mp3"},
            3: {"id": 3, "source": "remote", "cache_path": None},
            4: {"id": 4, "source": "soundcloud", "cache_path": ""},
        }
        return mapping.get(tid)

    monkeypatch.setattr(main, "get_track_by_id", fake_get_track)
    monkeypatch.setattr(main.cache_manager, "get_or_download", fake_get_or_download)

    resp = client.post("/cache/prefetch", json={"track_ids": [1, 2, 3, 4, 999]})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "scheduled": 2}
    assert scheduled_ids == [3, 4]


def test_playlists_endpoints():
    post_resp = client.post("/playlists", json={"name": "Test Favorites"})
    assert post_resp.status_code == 200
    data = post_resp.json()
    assert data["name"] == "Test Favorites"
    assert data["track_count"] == 0

    get_resp = client.get("/playlists")
    assert get_resp.status_code == 200
    playlists = get_resp.json()
    assert any(p["name"] == "Test Favorites" for p in playlists)

import io
import pytest
from fastapi.testclient import TestClient
from main import app
import db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_isolated_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_main.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    db.init_playlists_tables()
    return test_db


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


def test_bulk_folder_import(tmp_path):
    music_dir = tmp_path / "yandex_export"
    music_dir.mkdir()
    (music_dir / "01. Кино - Пачка сигарет.mp3").write_bytes(b"ID3-fake-audio-1")
    (music_dir / "02. Наутилус Помпилиус - Крылья.mp3").write_bytes(b"ID3-fake-audio-2")

    resp = client.post("/import/folder", json={
        "folder_path": str(music_dir),
        "target_mode": "both",
        "playlist_name": "Яндекс Коллекция"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 2
    assert data["playlist_id"] is not None

    pl_tracks = client.get(f"/playlists/{data['playlist_id']}/tracks").json()
    assert len(pl_tracks) == 2
    assert any(t["artist"] == "Кино" for t in pl_tracks)


def test_bulk_import_1000_plus_tracks_and_wave_influence(tmp_path):
    """Проверяет пакетный импорт 1050 треков в Любимое и плейлист, а также их влияние на профиль вкуса и Мою Волну."""
    tracks_payload = [
        {
            "path": str(tmp_path / f"track_{i}.mp3"),
            "title": f"Песня {i}",
            "artist": f"Исполнитель {i % 35}",
            "album": "Яндекс Коллекция",
            "genre": "Rock",
            "year": 2024,
            "track_number": i,
            "duration": 200.0,
            "cover_path": None,
            "source": "local",
        }
        for i in range(1, 1051)
    ]

    res = db.bulk_import_tracks(
        tracks_payload,
        target_mode="both",
        playlist_name="Импорт из Яндекс Музыки",
    )
    assert res["imported_count"] == 1050
    assert res["new_inserted"] == 1050
    assert res["playlist_id"] is not None
    assert res["taste_artists_count"] == 35

    # Все 1050 треков должны возвращаться в /favorites и в плейлисте (без старого лимита в 100 треков)
    fav_resp = client.get("/favorites")
    assert fav_resp.status_code == 200
    assert len(fav_resp.json()) == 1050

    pl_resp = client.get(f"/playlists/{res['playlist_id']}/tracks")
    assert pl_resp.status_code == 200
    assert len(pl_resp.json()) == 1050

    # Профиль вкуса и выборка для «Моей Волны»
    tp = client.get("/taste-profile").json()
    assert tp["total_tracks"] == 1050
    assert tp["liked_tracks"] == 1050
    assert len(tp["taste_artists"]) >= 30

    sampled = db.sample_taste_tracks(limit=20, max_per_artist=2)
    assert len(sampled) == 20


def test_chunked_upload_does_not_duplicate_playlists(tmp_path, monkeypatch):
    """Проверяет, что загрузка треков несколькими пачками с одним именем плейлиста не создаёт дубликаты плейлистов."""
    import importer
    monkeypatch.setattr(importer, "UPLOADS_DIR", tmp_path)
    import main
    monkeypatch.setattr(main, "UPLOADS_DIR", tmp_path)

    for batch_idx in range(3):
        files = [
            ("files", (f"{batch_idx * 2 + 1}. Кино - Песня {batch_idx}_1.mp3", io.BytesIO(b"ID3-audio-a" + bytes([batch_idx])), "audio/mpeg")),
            ("files", (f"{batch_idx * 2 + 2}. Сплин - Песня {batch_idx}_2.mp3", io.BytesIO(b"ID3-audio-b" + bytes([batch_idx])), "audio/mpeg")),
        ]
        r = client.post(
            "/import/upload",
            data={"target_mode": "both", "playlist_name": "Мои 1000 треков"},
            files=files,
        )
        assert r.status_code == 200

    playlists = client.get("/playlists").json()
    matching = [p for p in playlists if p["name"] == "Мои 1000 треков"]
    assert len(matching) == 1
    assert matching[0]["track_count"] == 6


def test_mobile_stream_range_request(tmp_path):
    """Проверяет поддержку HTTP Range (206 Partial Content) для воспроизведения и перемотки на iOS Safari / Android Chrome."""
    audio_file = tmp_path / "mobile_test.mp3"
    content = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 100  # 3600 bytes
    audio_file.write_bytes(content)

    res = db.bulk_import_tracks([{
        "path": str(audio_file),
        "title": "Mobile Song",
        "artist": "Mobile Artist",
        "duration": 120.0,
    }], target_mode="favorites")
    tid = client.get("/favorites").json()[0]["id"]

    # Range request bytes=0-99
    resp = client.get(f"/stream/{tid}", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers.get("accept-ranges") == "bytes"
    assert resp.headers.get("content-range") == f"bytes 0-99/{len(content)}"
    assert len(resp.content) == 100
    assert resp.content == content[:100]

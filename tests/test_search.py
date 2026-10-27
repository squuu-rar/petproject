import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from main import app, get_search_orchestrator

client = TestClient(app)

@pytest.fixture
def mock_orchestrator():
    orchestrator = MagicMock()
    # Имитируем возвращаемые данные
    orchestrator.search.return_value = [
        {
            "id": 1,
            "path": "local/path/1.mp3",
            "title": "Local Song",
            "artist": "Local Artist",
            "album": "Local Album",
            "genre": "Rock",
            "year": 2020,
            "track_number": 1,
            "duration": 180.0,
            "cover_path": None,
            "source": "local",
            "external_id": None,
            "cache_path": None
        },
        {
            "id": -1,
            "path": "https://www.youtube.com/watch?v=vid123",
            "title": "Remote Song",
            "artist": "Remote Artist",
            "album": "Remote Album",
            "genre": None,
            "year": None,
            "track_number": None,
            "duration": None,
            "cover_path": None,
            "source": "remote",
            "external_id": "vid123",
            "cache_path": None
        }
    ]
    return orchestrator

def test_search_endpoint_success(mock_orchestrator):
    # Подменяем зависимость в FastAPI
    app.dependency_overrides[get_search_orchestrator] = lambda: mock_orchestrator
    
    response = client.get("/search?q=test")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["title"] == "Local Song"
    assert data[1]["title"] == "Remote Song"
    assert data[1]["source"] == "remote"
    assert data[1]["id"] == -1
    
    app.dependency_overrides.clear()

def test_search_empty_query():
    # Проверка валидации FastAPI (min_length=1)
    response = client.get("/search?q=")
    assert response.status_code == 422

@patch("search_service.LocalSearchService.search")
@patch("search_service.RemoteSearchService.search")
def test_search_orchestrator_logic(mock_remote, mock_local):
    from search_service import SearchOrchestrator, LocalSearchService, RemoteSearchService
    
    mock_local.return_value = [{"id": 1, "title": "L", "artist": "A", "album": "B", "source": "local", "path": "p", "genre": None, "year": None, "track_number": None, "duration": None, "cover_path": None, "external_id": None, "cache_path": None}]
    mock_remote.return_value = [{"title": "R", "artist": "A", "album": "B", "video_id": "vid"}]
    
    orchestrator = SearchOrchestrator(LocalSearchService(), RemoteSearchService(MagicMock()))
    results = orchestrator.search("query")
    
    assert len(results) == 2
    assert results[0]["source"] == "local"
    assert results[1]["source"] == "remote"
    assert "youtube.com" in results[1]["path"]

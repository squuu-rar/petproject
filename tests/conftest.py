import pytest
from pathlib import Path
import db

@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_tracks.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    return test_db


@pytest.fixture(autouse=True)
def isolate_all_database_access(temp_db):
    """Even tests mocking only part of the engine must never reach the user's DB."""
    yield

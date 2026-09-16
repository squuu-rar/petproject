import pathlib
import pytest
from providers import LocalStorageProvider

def test_local_storage_provider_stream_url(tmp_path):
    """Test that get_stream_url returns the absolute path."""
    file_path = tmp_path / "test_song.mp3"
    file_path.touch()
    provider = LocalStorageProvider(file_path)
    
    assert provider.get_stream_url() == str(file_path.absolute())

def test_local_storage_provider_metadata_full(tmp_path, monkeypatch):
    """Test metadata extraction with all tags present."""
    file_path = tmp_path / "full_metadata.mp3"
    file_path.touch()

    class MockInfo:
        length = 245.5

    class MockFile:
        def __init__(self, tags, info):
            self.tags = tags
            self.info = info

    mock_tags = {
        "title": ["Song Title"],
        "artist": ["Artist Name"],
        "album": ["Album Name"],
        "genre": ["Rock"],
        "date": ["2022"],
        "tracknumber": ["5/12"],
    }
    mock_file = MockFile(mock_tags, MockInfo())

    # Mock mutagen.File to return our mock_file
    monkeypatch.setattr("mutagen.File", lambda path, easy=True: mock_file)

    provider = LocalStorageProvider(file_path)
    metadata = provider.get_metadata()

    assert metadata["title"] == "Song Title"
    assert metadata["artist"] == "Artist Name"
    assert metadata["album"] == "Album Name"
    assert metadata["genre"] == "Rock"
    assert metadata["year"] == 2022
    assert metadata["track_number"] == 5
    assert metadata["duration"] == 245.5
    assert metadata["source"] == "local"

def test_local_storage_provider_metadata_minimal(tmp_path, monkeypatch):
    """Test metadata extraction with minimal tags (fallback to filename)."""
    file_path = tmp_path / "fallback_track.mp3"
    file_path.touch()

    class MockInfo:
        length = 180.0

    class MockFile:
        def __init__(self, tags, info):
            self.tags = tags
            self.info = info

    # Empty tags
    mock_file = MockFile({}, MockInfo())
    monkeypatch.setattr("mutagen.File", lambda path, easy=True: mock_file)

    provider = LocalStorageProvider(file_path)
    metadata = provider.get_metadata()

    assert metadata["title"] == "fallback_track"
    assert metadata["artist"] is None
    assert metadata["duration"] == 180.0
    assert metadata["source"] == "local"

def test_local_storage_provider_invalid_file(tmp_path, monkeypatch):
    """Test behavior when file is not a valid audio file."""
    file_path = tmp_path / "corrupt.mp3"
    file_path.touch()

    # Simulate mutagen returning None for non-audio files
    monkeypatch.setattr("mutagen.File", lambda path, easy=True: None)

    provider = LocalStorageProvider(file_path)
    metadata = provider.get_metadata()

    assert metadata == {}

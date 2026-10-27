import pytest
from unittest.mock import MagicMock, patch
from providers import LocalStorageProvider, RemoteDiscoveryProvider, YoutubeStreamProvider

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
    monkeypatch.setattr("mutagen.File", lambda path, easy=True: None)

    provider = LocalStorageProvider(file_path)
    metadata = provider.get_metadata()

    assert metadata == {}

def test_remote_discovery_provider_search():
    """Test global search functionality."""
    mock_yt = MagicMock()
    mock_yt.search.return_value = [
        {
            "resultType": "song",
            "title": "Song 1",
            "artists": [{"name": "Artist 1"}],
            "album": {"name": "Album 1"},
            "videoId": "vid1"
        },
        {
            "resultType": "video",
            "title": "Video 1",
            "artists": [{"name": "Artist 1"}],
            "videoId": "vid2"
        }
    ]
    
    provider = RemoteDiscoveryProvider(mock_yt)
    results = provider.search("query")
    
    assert len(results) == 1
    assert results[0]["title"] == "Song 1"
    assert results[0]["artist"] == "Artist 1"
    assert results[0]["video_id"] == "vid1"

def test_remote_discovery_provider_metadata():
    """Test metadata retrieval for a specific video."""
    mock_yt = MagicMock()
    mock_yt.get_song.return_value = {
        "title": "Test Song",
        "artists": [{"name": "Test Artist"}],
        "album": {"name": "Test Album"},
        "duration": 200,
        "videoId": "abc123"
    }
    
    provider = RemoteDiscoveryProvider(mock_yt, video_id="abc123")
    metadata = provider.get_metadata()
    
    assert metadata["title"] == "Test Song"
    assert metadata["artist"] == "Test Artist"
    assert metadata["album"] == "Test Album"
    assert metadata["duration"] == 200
    assert metadata["source"] == "remote"
    assert metadata["external_id"] == "abc123"

def test_remote_discovery_provider_stream_url():
    """Test stream URL generation."""
    provider = RemoteDiscoveryProvider(MagicMock(), video_id="xyz789")
    assert provider.get_stream_url() == "https://www.youtube.com/watch?v=xyz789"

def test_remote_discovery_provider_no_id_error():
    """Test error when no video_id is provided."""
    provider = RemoteDiscoveryProvider(MagicMock())
    with pytest.raises(ValueError, match="No video_id provided"):
        provider.get_stream_url()
    assert provider.get_metadata() == {}

def test_remote_discovery_provider_api_error():
    """Test handling of API errors."""
    mock_yt = MagicMock()
    mock_yt.get_song.side_effect = Exception("API Error")
    
    provider = RemoteDiscoveryProvider(mock_yt, video_id="error_id")
    metadata = provider.get_metadata()
    
    assert metadata == {}

@patch("yt_dlp.YoutubeDL")
def test_youtube_stream_provider_success(mock_ydl_class):
    """Test successful extraction of direct URL and metadata."""
    # Setup mock
    mock_ydl_instance = mock_ydl_class.return_value.__enter__.return_value
    mock_ydl_instance.extract_info.return_value = {
        "url": "https://googlevideo.com/direct_audio_stream",
        "title": "Direct Stream Title",
        "uploader": "Direct Artist",
        "duration": 120,
    }

    provider = YoutubeStreamProvider(video_id="test_vid")
    
    # Test URL
    url = provider.get_stream_url()
    assert url == "https://googlevideo.com/direct_audio_stream"
    
    # Test Metadata
    metadata = provider.get_metadata()
    assert metadata["title"] == "Direct Stream Title"
    assert metadata["artist"] == "Direct Artist"
    assert metadata["duration"] == 120
    assert metadata["source"] == "remote"
    assert metadata["external_id"] == "test_vid"

@patch("yt_dlp.YoutubeDL")
def test_youtube_stream_provider_error(mock_ydl_class):
    """Test error handling when yt-dlp fails."""
    # Setup mock to raise exception
    mock_ydl_instance = mock_ydl_class.return_value.__enter__.return_value
    mock_ydl_instance.extract_info.side_effect = Exception("Network Error")

    provider = YoutubeStreamProvider(video_id="bad_vid")
    
    # Test URL error
    with pytest.raises(RuntimeError, match="Failed to extract stream URL"):
        provider.get_stream_url()
    
    # Test Metadata error (should return empty dict per implementation)
    metadata = provider.get_metadata()
    assert metadata == {}

def test_youtube_stream_provider_no_id():
    """Test error when no video_id is provided."""
    with pytest.raises(ValueError, match="video_id is required"):
        YoutubeStreamProvider(video_id="")

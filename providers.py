import abc
import pathlib
from typing import Any
import mutagen
import yt_dlp
from ytmusicapi import YTMusic

class StreamSource(abc.ABC):
    """Abstract base class for stream sources.

    Subclasses must provide implementations for ``get_stream_url`` and
    ``get_metadata``.
    """

    @abc.abstractmethod
    def get_stream_url(self) -> str:
        """Return the URL or file path for the stream.

        The method should resolve the source location and return a string that
        can be used by the player to access the audio data.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_metadata(self) -> dict[str, Any]:
        """Return metadata for the stream.

        The returned dictionary may contain keys such as ``title``, ``artist``,
        ``album``, ``genre``, ``year`` and any other information relevant to
        the source.
        """
        raise NotImplementedError()

class LocalStorageProvider(StreamSource):
    """Provider for reading audio files from the local file system."""

    def __init__(self, file_path: pathlib.Path):
        self.file_path = file_path

    def get_stream_url(self) -> str:
        """Return the absolute path of the file as a string."""
        return str(self.file_path.absolute())

    def get_metadata(self) -> dict[str, Any]:
        """Extract metadata from the local file using mutagen."""
        try:
            f = mutagen.File(self.file_path, easy=True)
        except Exception:
            return {}

        if f is None:
            return {}

        tags = getattr(f, "tags", None)
        if not hasattr(tags, "get"):
            tags = {}

        # Initialize data with defaults
        data = {
            "title": None,
            "artist": None,
            "album": None,
            "genre": None,
            "year": None,
            "track_number": None,
            "duration": None,
            "source": "local",
        }

        # Mapping based on scanner.py logic
        mapping = {
            "title": "title",
            "artist": "artist",
            "album": "album",
            "genre": "genre",
            "date": "year",
            "tracknumber": "track_number",
        }

        for easy_k, db_k in mapping.items():
            val = tags.get(easy_k)
            if val:
                data[db_k] = str(val[0])

        # Parse track number (e.g., "1/12" -> 1)
        if data.get("track_number"):
            try:
                data["track_number"] = int(str(data["track_number"]).split("/")[0])
            except (ValueError, TypeError):
                data["track_number"] = None

        # Parse year (e.g., "2023-01-01" -> 2023)
        if data.get("year"):
            try:
                data["year"] = int(str(data["year"])[:4])
            except (ValueError, TypeError):
                data["year"] = None

        # Get duration from info
        data["duration"] = getattr(getattr(f, "info", None), "length", None)

        # Fallback title to filename if no title tag is present
        if not data.get("title"):
            data["title"] = self.file_path.stem

        return data

class RemoteDiscoveryProvider(StreamSource):
    """Provider for searching and streaming tracks from YouTube Music."""

    def __init__(self, ytmusic: YTMusic, video_id: str = None):
        self.ytmusic = ytmusic
        self.video_id = video_id

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search for songs on YouTube Music."""
        try:
            results = self.ytmusic.search(query, filter="songs")
            search_results = []
            for r in results:
                if r.get("resultType") == "song":
                    search_results.append({
                        "title": r.get("title"),
                        "artist": r.get("artists", [{}])[0].get("name") if r.get("artists") else None,
                        "album": r.get("album", {}).get("name") if r.get("album") else None,
                        "video_id": r.get("videoId"),
                    })
            return search_results
        except Exception:
            return []

    def get_stream_url(self) -> str:
        """Return the YouTube watch URL."""
        if not self.video_id:
            raise ValueError("No video_id provided for RemoteDiscoveryProvider")
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def get_metadata(self) -> dict[str, Any]:
        """Fetch detailed metadata for the specific video."""
        if not self.video_id:
            return {}

        try:
            song = self.ytmusic.get_song(self.video_id)
            return {
                "title": song.get("title"),
                "artist": song.get("artists", [{}])[0].get("name") if song.get("artists") else None,
                "album": song.get("album", {}).get("name") if song.get("album") else None,
                "duration": song.get("duration"),
                "source": "remote",
                "external_id": self.video_id,
            }
        except Exception:
            return {}

class YoutubeStreamProvider(StreamSource):
    """Provider for extracting direct audio stream URLs using yt-dlp."""

    def __init__(self, video_id: str):
        if not video_id:
            raise ValueError("video_id is required")
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"

    def _get_ydl_info(self) -> dict[str, Any]:
        """Internal method to extract info using yt-dlp."""
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(self.url, download=False)

    def get_stream_url(self) -> str:
        """Return the direct audio stream URL."""
        try:
            info = self._get_ydl_info()
            return info.get("url")
        except Exception as e:
            raise RuntimeError(f"Failed to extract stream URL: {e}")

    def get_metadata(self) -> dict[str, Any]:
        """Return metadata extracted via yt-dlp."""
        try:
            info = self._get_ydl_info()
            return {
                "title": info.get("title"),
                "artist": info.get("uploader"),
                "album": None,  # YouTube doesn't provide album info in standard way
                "duration": info.get("duration"),
                "source": "remote",
                "external_id": self.video_id,
            }
        except Exception:
            return {}

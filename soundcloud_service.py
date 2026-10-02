import asyncio
from typing import List, Dict, Any, Optional
import yt_dlp

class SoundCloudSearchService:
    def __init__(self):
        self.ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "socket_timeout": 8,
        }

    def _sync_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        results = []
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                entries = info.get("entries") or []

                for entry in entries:
                    if not entry:
                        continue
                    url = entry.get("url") or entry.get("webpage_url")
                    title = entry.get("title")
                    uploader = entry.get("uploader") or entry.get("artist") or "SoundCloud Artist"
                    duration = entry.get("duration")
                    thumbnails = entry.get("thumbnails") or []
                    cover_url = thumbnails[-1]["url"] if thumbnails else None

                    results.append({
                        "id": -1,
                        "path": url,
                        "title": title,
                        "artist": uploader,
                        "album": "SoundCloud",
                        "genre": "SoundCloud",
                        "year": None,
                        "track_number": None,
                        "duration": float(duration) if duration else None,
                        "cover_path": cover_url,
                        "source": "soundcloud",
                        "external_id": url,
                        "cache_path": None,
                        "liked": False,
                        "last_accessed": None
                    })
        except Exception:
            pass
        return results

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        return await asyncio.to_thread(self._sync_search, query.strip(), limit)

    @staticmethod
    def get_stream_url(sc_url: str) -> Optional[str]:
        opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "socket_timeout": 10,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(sc_url, download=False)
                return info.get("url")
        except Exception:
            return None

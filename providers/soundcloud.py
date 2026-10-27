import asyncio
from typing import List, Dict, Any, Optional
import yt_dlp
from providers.base import StreamSource

class SoundCloudProvider(StreamSource):
    def __init__(self):
        self._ydl_search_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True
        }
        self._ydl_stream_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True
        }

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _execute():
            search_query = f"scsearch{limit}:{query}"
            with yt_dlp.YoutubeDL(self._ydl_search_opts) as ydl:
                try:
                    result = ydl.extract_info(search_query, download=False)
                    if not result or 'entries' not in result:
                        return []
                    
                    tracks = []
                    for entry in result['entries']:
                        if not entry:
                            continue
                        tracks.append({
                            "title": entry.get("title") or "Unknown Title",
                            "artist": entry.get("uploader") or entry.get("artist") or "SoundCloud Artist",
                            "album": "SoundCloud",
                            "duration": int(entry.get("duration") or 0),
                            "source": "remote",
                            "provider": "soundcloud",
                            "external_id": str(entry.get("id") or entry.get("url")),
                            "cover_path": entry.get("thumbnail") or "/static/img/cover-placeholder.png"
                        })
                    return tracks
                except Exception:
                    return []

        return await loop.run_in_executor(None, _execute)

    async def get_stream_url(self, external_id: str) -> Optional[str]:
        loop = asyncio.get_running_loop()

        def _extract():
            url = external_id if external_id.startswith("http") else f"https://api.soundcloud.com/tracks/{external_id}"
            with yt_dlp.YoutubeDL(self._ydl_stream_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                    return info.get("url")
                except Exception:
                    return None

        return await loop.run_in_executor(None, _extract)

    async def get_metadata(self, external_id: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _extract():
            url = external_id if external_id.startswith("http") else f"https://api.soundcloud.com/tracks/{external_id}"
            with yt_dlp.YoutubeDL(self._ydl_stream_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                    return {
                        "title": info.get("title"),
                        "artist": info.get("uploader") or info.get("artist"),
                        "duration": int(info.get("duration") or 0),
                        "cover_path": info.get("thumbnail")
                    }
                except Exception:
                    return None

        return await loop.run_in_executor(None, _extract)

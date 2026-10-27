from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yt_dlp

from ytdlp_opts import search_opts, stream_opts


def is_soundcloud_url(url: Optional[str]) -> bool:
    """True для soundcloud.com, api.soundcloud.com и их поддоменов."""
    if not url:
        return False
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except ValueError:
        return False
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


class SoundCloudSearchService:
    def __init__(self, timeout: int = 8):
        self.ydl_opts = search_opts(timeout)

    @staticmethod
    def _pick_cover(entry: Dict[str, Any]) -> Optional[str]:
        """Берём обложку среднего размера. Последний элемент списка — это оригинал
        (иногда несколько мегабайт), а в списках треков их десятки."""
        thumbs = [t for t in (entry.get("thumbnails") or []) if t and t.get("url")]
        by_id = {t.get("id"): t["url"] for t in thumbs}
        for wanted in ("t500x500", "crop", "t300x300", "large"):
            if wanted in by_id:
                return by_id[wanted]
        if entry.get("thumbnail"):
            return entry["thumbnail"]
        return thumbs[-1]["url"] if thumbs else None

    def _sync_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 25))

        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
        except Exception:
            return []

        results: List[Dict[str, Any]] = []
        seen_urls = set()
        seen_names = set()
        for entry in (info or {}).get("entries") or []:
            if not entry:
                continue
            # порядок url -> webpage_url сохранён: по этому значению треки уже записаны в БД
            url = entry.get("url") or entry.get("webpage_url")
            title = entry.get("title")
            if not url or not title:
                continue

            uploader = entry.get("uploader") or entry.get("artist") or "SoundCloud Artist"
            names_key = (_norm(title), _norm(uploader))
            # SoundCloud отдаёт один и тот же трек повторно (перезаливы, репосты)
            if url in seen_urls or names_key in seen_names:
                continue
            seen_urls.add(url)
            seen_names.add(names_key)

            duration = entry.get("duration")
            results.append({
                "id": -1,
                "path": url,
                "title": title,
                "artist": uploader,
                "album": "SoundCloud",
                # раньше тут было "SoundCloud": все треки попадали в один жанровый кластер волны
                "genre": None,
                "year": None,
                "track_number": None,
                "duration": float(duration) if duration else None,
                "cover_path": self._pick_cover(entry),
                "source": "soundcloud",
                "external_id": url,
                "cache_path": None,
                "liked": False,
                "last_accessed": None,
            })
        return results

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        return await asyncio.to_thread(self._sync_search, query.strip(), limit)

    @staticmethod
    def get_stream_url(sc_url: str) -> Optional[str]:
        """Прямая ссылка, которую можно отдать <audio>, либо None.

        HLS (.m3u8) возвращать нельзя: Chrome и Firefox его не проигрывают, и редирект
        на такую ссылку заканчивался ошибкой плеера и пропуском трека. В этом случае
        None означает «проигрывать через кэш».
        """
        try:
            with yt_dlp.YoutubeDL(stream_opts()) as ydl:
                info = ydl.extract_info(sc_url, download=False)
        except Exception:
            return None

        url = (info or {}).get("url")
        protocol = str((info or {}).get("protocol") or "").lower()
        if not url or "m3u8" in protocol or "dash" in protocol or ".m3u8" in str(url).lower():
            return None
        return url

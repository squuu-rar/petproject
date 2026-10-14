import os
import urllib.parse
import urllib.request
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from pathlib import Path

def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

_load_dotenv()

_AVATAR_CACHE: Dict[str, Optional[str]] = {}
_LASTFM_PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"


class LastFMService:
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LASTFM_API_KEY", "")

    def _sync_request(self, params: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        if not self.api_key:
            return {}
        params["api_key"] = self.api_key
        params["format"] = "json"
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "MusicStreamApp/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return {}

    def get_artist_avatar(self, artist: str, timeout: float = 4.0) -> Optional[str]:
        if not artist or not self.api_key:
            return None
        data = self._sync_request(
            {"method": "artist.getinfo", "artist": artist.strip(), "autocorrect": 1},
            timeout=timeout,
        )
        images = data.get("artist", {}).get("image", [])
        if isinstance(images, list):
            for img in reversed(images):
                url = (img or {}).get("#text")
                if url and _LASTFM_PLACEHOLDER not in url:
                    return url
        return None

    async def get_similar_tracks(self, artist: str, track: str, limit: int = 15) -> List[Dict[str, str]]:
        if not artist or not track:
            return []

        def _fetch():
            data = self._sync_request({
                "method": "track.getsimilar",
                "artist": artist,
                "track": track,
                "limit": limit,
                "autocorrect": 1
            })
            sim_tracks = data.get("similartracks", {}).get("track", [])
            if isinstance(sim_tracks, dict):
                sim_tracks = [sim_tracks]

            results = []
            for item in sim_tracks:
                a_name = item.get("artist", {}).get("name") if isinstance(item.get("artist"), dict) else item.get("artist")
                t_name = item.get("name")
                if a_name and t_name:
                    results.append({"artist": a_name, "title": t_name})
            return results

        return await asyncio.to_thread(_fetch)

    async def get_loved_tracks(self, username: Optional[str] = None, limit: int = 50) -> List[Dict[str, str]]:
        user = username or os.environ.get("LASTFM_USERNAME", "")
        if not user:
            return []

        def _fetch():
            data = self._sync_request({
                "method": "user.getlovedtracks",
                "user": user,
                "limit": limit
            })
            tracks = data.get("lovedtracks", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            results = []
            for item in tracks:
                a_name = item.get("artist", {}).get("name")
                t_name = item.get("name")
                if a_name and t_name:
                    results.append({"artist": a_name, "title": t_name})
            return results

        return await asyncio.to_thread(_fetch)


def get_remote_artist_avatar(artist_name: str, ytmusic: Optional[Any] = None, timeout: float = 4.0) -> Optional[str]:
    clean = (artist_name or "").strip()
    if not clean:
        return None
    cache_key = clean.casefold()
    if cache_key in _AVATAR_CACHE:
        return _AVATAR_CACHE[cache_key]

    avatar = LastFMService().get_artist_avatar(clean, timeout=timeout)

    if not avatar and ytmusic is not None:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(ytmusic.search, clean, filter="artists", limit=1)
            results = fut.result(timeout=timeout) or []
            if results and isinstance(results, list):
                thumbs = results[0].get("thumbnails") or []
                if thumbs and thumbs[-1].get("url"):
                    avatar = thumbs[-1]["url"]
        except Exception:
            avatar = None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    if avatar:
        _AVATAR_CACHE[cache_key] = avatar
    return avatar

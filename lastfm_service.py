import os
import urllib.parse
import urllib.request
import json
import asyncio
from typing import List, Dict, Any, Optional

class LastFMService:
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LASTFM_API_KEY", "")

    def _sync_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            return {}
        params["api_key"] = self.api_key
        params["format"] = "json"
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "MusicStreamApp/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return {}

    async def get_similar_tracks(self, artist: str, track: str, limit: int = 15) -> List[Dict[str, str]]:
        if not artist or not track or not self.api_key:
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
        if not user or not self.api_key:
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

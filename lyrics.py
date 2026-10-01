import json
import urllib.parse
import urllib.request
from typing import Any, Dict

def fetch_lyrics(artist: str, title: str, duration: float | None = None) -> Dict[str, Any]:
    if not artist or not title:
        return {}

    headers = {"User-Agent": "MusicStreamApp/1.0"}
    base_url = "https://lrclib.net/api/get"
    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(duration)
    
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    
    data = None
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    if not data or not (data.get("syncedLyrics") or data.get("plainLyrics")):
        search_query = f"{artist} {title}"
        search_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(search_query)}"
        search_req = urllib.request.Request(search_url, headers=headers)
        try:
            with urllib.request.urlopen(search_req, timeout=4) as resp:
                if resp.status == 200:
                    items = json.loads(resp.read().decode("utf-8"))
                    if items and isinstance(items, list):
                        data = items[0]
        except Exception:
            pass

    if not data:
        return {}

    synced = data.get("syncedLyrics")
    plain = data.get("plainLyrics")
    return {
        "id": data.get("id"),
        "trackName": data.get("trackName"),
        "artistName": data.get("artistName"),
        "syncedLyrics": synced,
        "plainLyrics": plain,
        "lyrics": synced or plain
    }

import json
import urllib.parse
import urllib.request
from typing import Any, Dict

def fetch_lyrics(artist: str, title: str, duration: float | None = None) -> Dict[str, Any]:
    if not artist or not title:
        return {}

    # 1. Попытка точного поиска по метаданным
    base_url = "https://lrclib.net/api/get"
    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(duration)
    
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "MusicStreamApp/1.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    # 2. Фоллбек: общий текстовый поиск по артисту и названию
    search_query = f"{artist} {title}"
    search_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(search_query)}"
    search_req = urllib.request.Request(search_url, headers={"User-Agent": "MusicStreamApp/1.0"})
    
    try:
        with urllib.request.urlopen(search_req, timeout=4) as resp:
            if resp.status == 200:
                items = json.loads(resp.read().decode("utf-8"))
                if items and isinstance(items, list):
                    return items[0]
    except Exception:
        pass

    return {}

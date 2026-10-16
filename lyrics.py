import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def _clean_term(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\s*[\(\[][^)\]]*(?:official|video|audio|lyric|live|remaster|hd|4k|hq|prod|feat|ft|clip|клип|라이브)[^)\]]*[\)\]]", "", text, flags=re.IGNORECASE)
    s = re.sub(r"\s*(?:feat\.?|ft\.?|prod\.?\s+by)\s+.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*[-—–]\s*(?:official|video|audio|live|remastered).*$", "", s, flags=re.IGNORECASE)
    return " ".join(s.split()).strip()


def _http_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "MusicStreamApp/2.0 (https://lrclib.net)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    return None


def _pick_best(items: List[Dict[str, Any]], target_duration: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if not items or not isinstance(items, list):
        return None
    valid = [x for x in items if isinstance(x, dict) and (x.get("syncedLyrics") or x.get("plainLyrics"))]
    if not valid:
        return None

    def score(item: Dict[str, Any]):
        has_synced = 1 if item.get("syncedLyrics") else 0
        dur = item.get("duration")
        dur_diff = abs(float(dur) - float(target_duration)) if (dur and target_duration) else 999.0
        return (-has_synced, dur_diff)

    valid.sort(key=score)
    return valid[0]


def fetch_lyrics(artist: str, title: str, duration: float | None = None) -> Dict[str, Any]:
    raw_artist = (artist or "").strip()
    raw_title = (title or "").strip()
    if not raw_title and not raw_artist:
        return {}

    # Если в title записано "Артист - Песня", разделяем
    if (not raw_artist or raw_artist.lower() in ("unknown artist", "soundcloud artist", "неизвестный исполнитель")) and (" - " in raw_title or " — " in raw_title):
        parts = re.split(r"\s+[-—–]\s+", raw_title, maxsplit=1)
        if len(parts) == 2:
            raw_artist, raw_title = parts[0].strip(), parts[1].strip()

    clean_artist = _clean_term(raw_artist)
    clean_title = _clean_term(raw_title) or raw_title

    data: Optional[Dict[str, Any]] = None

    # 1. Точный запрос /api/get
    if clean_artist and clean_title:
        params: Dict[str, Any] = {"artist_name": clean_artist, "track_name": clean_title}
        if duration and duration > 0:
            params["duration"] = int(duration)
        try:
            res = _http_json(f"https://lrclib.net/api/get?{urllib.parse.urlencode(params)}", timeout=4.5)
            if isinstance(res, dict) and (res.get("syncedLyrics") or res.get("plainLyrics")):
                data = res
        except Exception:
            pass

    # 2. Структурированный поиск по track_name + artist_name
    if not data and clean_title:
        try:
            q_params = {"track_name": clean_title}
            if clean_artist:
                q_params["artist_name"] = clean_artist
            items = _http_json(f"https://lrclib.net/api/search?{urllib.parse.urlencode(q_params)}", timeout=5.0)
            data = _pick_best(items, duration)
        except Exception:
            pass

    # 3. Широкий текстовый поиск /api/search?q=
    if not data:
        for query in dict.fromkeys([f"{clean_artist} {clean_title}".strip(), f"{raw_artist} {raw_title}".strip(), clean_title]):
            if not query:
                continue
            try:
                items = _http_json(f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}", timeout=5.0)
                data = _pick_best(items, duration)
                if data:
                    break
            except Exception:
                pass

    if not data:
        return {}

    synced = data.get("syncedLyrics")
    plain = data.get("plainLyrics")
    return {
        "id": data.get("id"),
        "trackName": data.get("trackName") or clean_title,
        "artistName": data.get("artistName") or clean_artist,
        "duration": data.get("duration") or duration,
        "syncedLyrics": synced,
        "plainLyrics": plain,
        "lyrics": synced or plain,
    }

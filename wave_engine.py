import asyncio
from typing import List, Dict, Any, Optional
from ytmusicapi import YTMusic
from db import get_db, get_wave_exclusions
from lastfm_service import LastFMService

class WaveEngine:
    MAX_PARALLEL_SEARCHES = 4

    def __init__(self):
        self.ytmusic = YTMusic()
        self.lastfm = LastFMService()

    @staticmethod
    def _parse_duration(duration_str: Optional[str]) -> Optional[float]:
        if not duration_str or not isinstance(duration_str, str):
            return None
        try:
            parts = duration_str.strip().split(":")
            sec = 0.0
            for p in parts:
                sec = sec * 60 + float(p)
            return sec
        except Exception:
            return None

    def _resolve_seed_tracks(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        conn = get_db()
        try:
            placeholders = ','.join(['?'] * len(seed_track_ids))
            query = f"SELECT id, artist, title, external_id FROM tracks WHERE id IN ({placeholders})"
            seed_rows = conn.execute(query, seed_track_ids).fetchall()
            return [dict(row) for row in seed_rows]
        finally:
            conn.close()

    def _search_youtube_stream(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        query = f"{artist} - {title}"
        try:
            res = self.ytmusic.search(query=query, filter="songs", limit=1)
            if not res:
                return None
            item = res[0]
            v_id = item.get("videoId")
            if not v_id:
                return None

            artists = item.get("artists", [])
            artist_name = artists[0].get("name") if artists else artist
            album_info = item.get("album") or {}
            album_name = album_info.get("name") if isinstance(album_info, dict) else "-"
            thumbs = item.get("thumbnails", [])
            cover_url = thumbs[-1]["url"] if thumbs else None
            dur = self._parse_duration(item.get("duration"))

            return {
                "id": -1,
                "external_id": v_id,
                "path": f"https://www.youtube.com/watch?v={v_id}",
                "title": item.get("title") or title,
                "artist": artist_name,
                "album": album_name,
                "genre": "Wave (Last.fm)",
                "year": None,
                "track_number": None,
                "cover_path": cover_url,
                "duration": dur,
                "source": "remote",
                "cache_path": None,
                "liked": False,
                "last_accessed": None
            }
        except Exception:
            return None

    async def generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        if not seed_track_ids:
            return []

        seeds = await asyncio.to_thread(self._resolve_seed_tracks, seed_track_ids)
        if not seeds:
            return []

        seed_ext_ids = {s["external_id"] for s in seeds if s.get("external_id")}
        blocked_ids = seed_ext_ids | await asyncio.to_thread(get_wave_exclusions)

        sem = asyncio.Semaphore(self.MAX_PARALLEL_SEARCHES)

        async def resolve(sim: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                return await asyncio.to_thread(self._search_youtube_stream, sim["artist"], sim["title"])

        async def similar_for_seed(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
            similar = await self.lastfm.get_similar_tracks(seed["artist"], seed["title"], limit=4)
            resolved = await asyncio.gather(*(resolve(sim) for sim in similar), return_exceptions=True)
            return [r for r in resolved if isinstance(r, dict)]

        def get_yt_radio() -> List[Dict[str, Any]]:
            found: List[Dict[str, Any]] = []
            for s in seeds[:2]:
                ext_id = s.get("external_id")
                if not ext_id:
                    continue
                try:
                    playlist = self.ytmusic.get_watch_playlist(videoId=ext_id, radio=True, limit=10)
                except Exception:
                    continue
                items = playlist.get("tracks") or playlist.get("contents") or []
                for item in items:
                    v_id = item.get("videoId")
                    title = item.get("title")
                    if not v_id or not title:
                        continue
                    artists = item.get("artists", [])
                    artist_name = artists[0].get("name") if artists else "Unknown Artist"
                    album = item.get("album", {}).get("name") if item.get("album") else "-"
                    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                    found.append({
                        "id": -1,
                        "external_id": v_id,
                        "path": f"https://www.youtube.com/watch?v={v_id}",
                        "title": title,
                        "artist": artist_name,
                        "album": album,
                        "genre": "Wave (Radio)",
                        "year": None,
                        "track_number": None,
                        "cover_path": thumbs[-1]["url"] if thumbs else None,
                        "duration": self._parse_duration(item.get("length")),
                        "source": "remote",
                        "cache_path": None,
                        "liked": False,
                        "last_accessed": None
                    })
            return found

        radio_task = asyncio.create_task(asyncio.to_thread(get_yt_radio))
        lastfm_groups = await asyncio.gather(
            *(similar_for_seed(s) for s in seeds[:3] if s.get("artist") and s.get("title")),
            return_exceptions=True,
        )
        try:
            radio_tracks = await radio_task
        except Exception:
            radio_tracks = []

        lastfm_tracks = [t for group in lastfm_groups if isinstance(group, list) for t in group]

        merged: List[Dict[str, Any]] = []
        for i in range(max(len(lastfm_tracks), len(radio_tracks))):
            if i < len(lastfm_tracks):
                merged.append(lastfm_tracks[i])
            if i < len(radio_tracks):
                merged.append(radio_tracks[i])

        candidates: Dict[str, Dict[str, Any]] = {}
        for t in merged:
            ext_id = t["external_id"]
            if ext_id in blocked_ids or ext_id in candidates:
                continue
            candidates[ext_id] = t

        return list(candidates.values())

    async def generate_wave(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = get_db()
        try:
            recent = conn.execute("""
                SELECT track_id FROM history 
                WHERE event IN ('play', 'finish') ORDER BY timestamp DESC LIMIT 2
            """).fetchall()
            liked = conn.execute("SELECT id as track_id FROM tracks WHERE liked = 1 ORDER BY RANDOM() LIMIT 2").fetchall()
            raw_seeds = [r["track_id"] for r in (list(recent) + list(liked))]
            if not raw_seeds:
                fallback = conn.execute("SELECT id as track_id FROM tracks ORDER BY id DESC LIMIT 3").fetchall()
                raw_seeds = [r["track_id"] for r in fallback]
        finally:
            conn.close()

        seeds = list(dict.fromkeys(raw_seeds))[:3]
        candidates = await self.generate_candidates(seeds)
        return candidates[:limit]

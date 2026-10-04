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

    def _search_youtube_stream(self, target_artist: str, target_title: str) -> Optional[Dict[str, Any]]:
        """Ищет трек строго по связке артист + песня с обязательной валидацией исполнителя."""
        # Кавычки для артиста гарантируют, что YouTube не проигнорирует имя группы
        query = f'"{target_artist}" {target_title}'
        try:
            res = self.ytmusic.search(query=query, filter="songs", limit=3)
            if not res:
                # Мягкий поиск без кавычек
                res = self.ytmusic.search(query=f"{target_artist} {target_title}", filter="songs", limit=3)
            if not res:
                return None

            target_artist_lower = target_artist.lower().strip()
            best_match = None

            for item in res:
                v_id = item.get("videoId")
                if not v_id:
                    continue

                artists = item.get("artists", [])
                item_artist = (artists[0].get("name") if artists else "").lower().strip()

                # Проверяем, что в выдаче действительно нужный исполнитель
                if target_artist_lower in item_artist or item_artist in target_artist_lower:
                    best_match = item
                    break

            if not best_match:
                # Если ни один из 3 результатов не соответствует исполнителю — отбрасываем
                return None

            v_id = best_match.get("videoId")
            if not v_id:
                return None

            artists = best_match.get("artists", [])
            artist_name = artists[0].get("name") if artists else target_artist
            album_info = best_match.get("album") or {}
            album_name = album_info.get("name") if isinstance(album_info, dict) else "-"
            thumbs = best_match.get("thumbnails", [])
            cover_url = thumbs[-1]["url"] if thumbs else None
            dur = self._parse_duration(best_match.get("duration"))

            return {
                "id": -1,
                "external_id": v_id,
                "path": f"https://www.youtube.com/watch?v={v_id}",
                "title": best_match.get("title") or target_title,
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

    async def _get_similar_by_artist(self, artist: str, forbidden_titles: set) -> List[Dict[str, str]]:
        """Находит похожих артистов в Last.fm и берёт их лучшие треки."""
        if not artist or not self.lastfm.api_key:
            return []

        def _fetch():
            # 1. Запрашиваем 5 похожих групп
            data = self.lastfm._sync_request({
                "method": "artist.getsimilar",
                "artist": artist,
                "limit": 5,
                "autocorrect": 1
            })
            similar_artists = data.get("similarartists", {}).get("artist", [])
            if isinstance(similar_artists, dict):
                similar_artists = [similar_artists]

            recommendations = []
            for sim in similar_artists:
                sim_name = sim.get("name")
                if not sim_name or sim_name.lower() == artist.lower():
                    continue

                # 2. Забираем топ-2 главных трека этой похожей группы
                top_data = self.lastfm._sync_request({
                    "method": "artist.gettoptracks",
                    "artist": sim_name,
                    "limit": 2,
                    "autocorrect": 1
                })
                tracks = top_data.get("toptracks", {}).get("track", [])
                if isinstance(tracks, dict):
                    tracks = [tracks]

                for t in tracks:
                    t_title = t.get("name")
                    # Защита от дублей названий: не берем треки с совпадающим с сидом названием
                    if t_title and t_title.lower() not in forbidden_titles:
                        recommendations.append({"artist": sim_name, "title": t_title})

            return recommendations

        return await asyncio.to_thread(_fetch)

    async def generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        if not seed_track_ids:
            return []

        seeds = await asyncio.to_thread(self._resolve_seed_tracks, seed_track_ids)
        if not seeds:
            return []

        seed_ext_ids = {s["external_id"] for s in seeds if s.get("external_id")}
        seed_titles = {s["title"].lower().strip() for s in seeds if s.get("title")}
        blocked_ids = seed_ext_ids | await asyncio.to_thread(get_wave_exclusions)

        sem = asyncio.Semaphore(self.MAX_PARALLEL_SEARCHES)

        async def resolve(item: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                return await asyncio.to_thread(self._search_youtube_stream, item["artist"], item["title"])

        # 1. Рекомендации по графу похожих исполнителей Last.fm
        candidate_tasks = []
        for s in seeds[:3]:
            if s.get("artist"):
                candidate_tasks.append(self._get_similar_by_artist(s["artist"], seed_titles))

        gathered_groups = await asyncio.gather(*candidate_tasks, return_exceptions=True)
        raw_candidates = [t for group in gathered_groups if isinstance(group, list) for t in group]

        # 2. Параллельный поиск аудиопотоков для найденных треков
        resolved = await asyncio.gather(*(resolve(c) for c in raw_candidates), return_exceptions=True)
        lastfm_tracks = [r for r in resolved if isinstance(r, dict)]

        # 3. Дополнительный фоллбэк: YouTube Music Radio
        def get_yt_radio() -> List[Dict[str, Any]]:
            found: List[Dict[str, Any]] = []
            for s in seeds[:2]:
                ext_id = s.get("external_id")
                if not ext_id:
                    continue
                try:
                    playlist = self.ytmusic.get_watch_playlist(videoId=ext_id, radio=True, limit=8)
                except Exception:
                    continue
                items = playlist.get("tracks") or playlist.get("contents") or []
                for item in items:
                    v_id = item.get("videoId")
                    title = item.get("title")
                    if not v_id or not title:
                        continue
                    # Отсекаем треки с тем же самым названием
                    if title.lower().strip() in seed_titles:
                        continue
                    artists = item.get("artists", [])
                    artist_name = artists[0].get("name") if artists else "Unknown Artist"
                    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                    found.append({
                        "id": -1,
                        "external_id": v_id,
                        "path": f"https://www.youtube.com/watch?v={v_id}",
                        "title": title,
                        "artist": artist_name,
                        "album": item.get("album", {}).get("name") if item.get("album") else "-",
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

        try:
            radio_tracks = await asyncio.to_thread(get_yt_radio)
        except Exception:
            radio_tracks = []

        # Чередуем похожие группы Last.fm и радио YouTube
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
                SELECT track_id FROM history h
                JOIN tracks t ON t.id = h.track_id
                WHERE h.event IN ('play', 'finish') 
                  AND t.artist IS NOT NULL 
                  AND LENGTH(t.title) > 2
                ORDER BY h.timestamp DESC LIMIT 2
            """).fetchall()

            liked = conn.execute("""
                SELECT id as track_id FROM tracks 
                WHERE liked = 1 
                  AND artist IS NOT NULL 
                  AND LENGTH(title) > 2
                ORDER BY RANDOM() LIMIT 2
            """).fetchall()

            raw_seeds = [r["track_id"] for r in (list(recent) + list(liked))]
            if not raw_seeds:
                fallback = conn.execute("""
                    SELECT id as track_id FROM tracks 
                    WHERE artist IS NOT NULL AND LENGTH(title) > 2 
                    ORDER BY id DESC LIMIT 2
                """).fetchall()
                raw_seeds = [r["track_id"] for r in fallback]
        finally:
            conn.close()

        seeds = list(dict.fromkeys(raw_seeds))[:3]
        candidates = await self.generate_candidates(seeds)
        return candidates[:limit]

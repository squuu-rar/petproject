import asyncio
from typing import List, Dict, Any, Optional
from ytmusicapi import YTMusic
from db import get_db

class WaveEngine:
    """Движок генерации треков-кандидатов для 'Моей волны' через YouTube Music Radio."""

    def __init__(self):
        self.ytmusic = YTMusic()

    @staticmethod
    def _parse_duration(duration_str: Optional[str]) -> Optional[float]:
        """Конвертирует строку вида '3:45' или '1:02:10' в секунды (float)."""
        if not duration_str or not isinstance(duration_str, str):
            return None
        try:
            parts = duration_str.strip().split(":")
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            return seconds
        except (ValueError, TypeError):
            return None

    def _resolve_seed_external_ids(self, seed_track_ids: List[int]) -> List[str]:
        """
        Извлекает external_id для сид-треков.
        Если трек локальный (external_id пуст), ищет соответствие на YouTube по 'артист + название'.
        """
        conn = get_db()
        try:
            placeholders = ','.join(['?'] * len(seed_track_ids))
            query = f"SELECT id, artist, title, external_id, source FROM tracks WHERE id IN ({placeholders})"
            seed_rows = conn.execute(query, seed_track_ids).fetchall()
        finally:
            conn.close()

        resolved_ids = []
        for row in seed_rows:
            ext_id = row["external_id"]
            if ext_id:
                resolved_ids.append(ext_id)
            elif row["artist"] and row["title"]:
                try:
                    q = f"{row['artist']} - {row['title']}"
                    search_res = self.ytmusic.search(query=q, filter="songs", limit=1)
                    if search_res and search_res[0].get("videoId"):
                        resolved_ids.append(search_res[0]["videoId"])
                except Exception:
                    continue
        return resolved_ids

    def _sync_generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        """Синхронная логика сбора кандидатов (выполняется в отдельном потоке)."""
        external_ids = self._resolve_seed_external_ids(seed_track_ids)
        if not external_ids:
            return []

        seed_set = set(external_ids)
        unique_candidates: Dict[str, Dict[str, Any]] = {}

        for ext_id in external_ids:
            try:
                playlist = self.ytmusic.get_watch_playlist(videoId=ext_id, radio=True, limit=25)
                items = playlist.get('tracks') or playlist.get('contents') or []

                for item in items:
                    v_id = item.get('videoId')
                    if not v_id or v_id in unique_candidates or v_id in seed_set:
                        continue

                    title = item.get('title')
                    if not title:
                        continue

                    artists = item.get('artists', [])
                    artist_name = artists[0].get('name') if artists and isinstance(artists, list) else None

                    album_obj = item.get('album') or {}
                    album_name = album_obj.get('name') if isinstance(album_obj, dict) else None

                    thumbnails = item.get('thumbnail') or item.get('thumbnails') or []
                    cover_url = thumbnails[-1]['url'] if thumbnails and isinstance(thumbnails, list) else None

                    length_raw = item.get('length')
                    duration_sec = self._parse_duration(length_raw)

                    unique_candidates[v_id] = {
                        "external_id": v_id,
                        "path": f"https://www.youtube.com/watch?v={v_id}",
                        "title": title,
                        "artist": artist_name or "Unknown Artist",
                        "album": album_name or "-",
                        "source": "remote",
                        "cover_path": cover_url,
                        "duration": duration_sec
                    }
            except Exception:
                continue

        return list(unique_candidates.values())

    async def generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Асинхронная точка входа.
        Запускает блокирующие сетевые вызовы в фоновом потоке, не вешая Event Loop сервера.
        """
        if not seed_track_ids:
            return []

        return await asyncio.to_thread(self._sync_generate_candidates, seed_track_ids)

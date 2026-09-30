import sqlite3
from typing import List, Dict, Any
from ytmusicapi import YTMusic
from providers import RemoteDiscoveryProvider
from db import get_db

class LocalSearchService:
    def search(self, query: str) -> List[Dict[str, Any]]:
        if not query:
            return []
        
        words = query.strip().split()
        if not words:
            return []

        # Поиск по всем отдельным словам в запросе
        clauses = []
        params = []
        for word in words:
            term = f"%{word}%"
            clauses.append("(LOWER(title) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?) OR LOWER(album) LIKE LOWER(?))")
            params.extend([term, term, term])

        sql = f"SELECT * FROM tracks WHERE {' AND '.join(clauses)}"
        conn = get_db()
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

class RemoteSearchService:
    def __init__(self, ytmusic: YTMusic):
        self.provider = RemoteDiscoveryProvider(ytmusic)

    def search(self, query: str) -> List[Dict[str, Any]]:
        if not query:
            return []
        return self.provider.search(query)

class SearchOrchestrator:
    def __init__(self, local_service: LocalSearchService, remote_service: RemoteSearchService):
        self.local_service = local_service
        self.remote_service = remote_service

    def search(self, query: str) -> List[Dict[str, Any]]:
        # 1. Локальный поиск
        local_results = self.local_service.search(query)
        local_ext_ids = {t.get("external_id") for t in local_results if t.get("external_id")}
        local_keys = {(t.get("title", "").strip().lower(), t.get("artist", "").strip().lower()) for t in local_results}

        # 2. Удаленный поиск
        remote_results = self.remote_service.search(query)
        
        # 3. Синхронизация с базой данных
        conn = get_db()
        mapped_remote = []

        try:
            for r in remote_results:
                video_id = r.get("video_id")
                title = (r.get("title") or "").strip()
                artist = (r.get("artist") or "").strip()
                remote_cover = r.get("cover_path")
                remote_duration = r.get("duration")

                # Проверяем, есть ли уже этот трек в базе (по external_id или паре title+artist)
                db_track = None
                if video_id:
                    row = conn.execute("SELECT * FROM tracks WHERE external_id = ?", (video_id,)).fetchone()
                    if row:
                        db_track = dict(row)

                if not db_track and title and artist:
                    row = conn.execute(
                        "SELECT * FROM tracks WHERE LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)", 
                        (title, artist)
                    ).fetchone()
                    if row:
                        db_track = dict(row)

                # Если трек найден в БД
                if db_track:
                    track_id = db_track["id"]
                    is_liked = bool(db_track.get("liked"))
                    
                    # Если в базе не было обложки или длительности — дописываем из YouTube
                    need_update = False
                    new_cover = db_track.get("cover_path")
                    new_dur = db_track.get("duration")

                    if not new_cover and remote_cover:
                        new_cover = remote_cover
                        need_update = True
                    if not new_dur and remote_duration:
                        new_dur = remote_duration
                        need_update = True

                    if need_update:
                        conn.execute(
                            "UPDATE tracks SET cover_path = ?, duration = ? WHERE id = ?",
                            (new_cover, new_dur, track_id)
                        )
                        conn.commit()

                    # Если трек уже показан в локальных результатах, не дублируем его в онлайн-секции
                    if video_id in local_ext_ids or (title.lower(), artist.lower()) in local_keys:
                        continue

                    mapped_remote.append({
                        "id": track_id,
                        "path": db_track.get("path") or f"https://www.youtube.com/watch?v={video_id}",
                        "title": title,
                        "artist": artist,
                        "album": db_track.get("album") or r.get("album"),
                        "genre": db_track.get("genre"),
                        "year": db_track.get("year"),
                        "track_number": db_track.get("track_number"),
                        "duration": new_dur,
                        "cover_path": new_cover or remote_cover,
                        "source": db_track.get("source") or "remote",
                        "external_id": video_id or db_track.get("external_id"),
                        "cache_path": db_track.get("cache_path"),
                        "liked": is_liked
                    })
                else:
                    # Трека нет в базе: новый удаленный трек
                    mapped_remote.append({
                        "id": -1,
                        "path": f"https://www.youtube.com/watch?v={video_id}",
                        "title": title,
                        "artist": artist,
                        "album": r.get("album"),
                        "genre": None,
                        "year": None,
                        "track_number": None,
                        "duration": remote_duration,
                        "cover_path": remote_cover,
                        "source": "remote",
                        "external_id": video_id,
                        "cache_path": None,
                        "liked": False
                    })
        finally:
            conn.close()

        return local_results + mapped_remote

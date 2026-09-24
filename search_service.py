import sqlite3
from typing import List, Dict, Any
from ytmusicapi import YTMusic
from providers import RemoteDiscoveryProvider
from db import get_db

class LocalSearchService:
    def search(self, query: str) -> List[Dict[str, Any]]:
        if not query:
            return []
        
        search_term = f"%{query}%"
        sql = """
            SELECT * FROM tracks 
            WHERE LOWER(title) LIKE LOWER(?) 
               OR LOWER(artist) LIKE LOWER(?) 
               OR LOWER(album) LIKE LOWER(?)
        """
        conn = get_db()
        try:
            cursor = conn.execute(sql, (search_term, search_term, search_term))
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
        # 1. Ищем локально
        local_results = self.local_service.search(query)
        
        # 2. Ищем удаленно
        remote_results = self.remote_service.search(query)
        
        # 3. Маппим удаленные результаты в формат TrackRead
        mapped_remote = []
        for r in remote_results:
            mapped_remote.append({
                "id": -1,  # Заглушка для удаленных треков
                "path": f"https://www.youtube.com/watch?v={r.get('video_id')}",
                "title": r.get("title"),
                "artist": r.get("artist"),
                "album": r.get("album"),
                "genre": None,
                "year": None,
                "track_number": None,
                "duration": r.get("duration"),
                "cover_path": r.get("cover_path"),
                "source": "remote",
                "external_id": r.get("video_id"),
                "cache_path": None
            })
            
        return local_results + mapped_remote

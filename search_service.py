import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from ytmusicapi import YTMusic
from db import get_db
from soundcloud_service import SoundCloudSearchService
from providers import RemoteDiscoveryProvider

class LocalSearchService:
    def search(self, query: str) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        words = query.strip().split()
        if not words:
            return []

        clauses = []
        params = []
        for word in words:
            term = f"%{word}%"
            clauses.append("(LOWER(title) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?) OR LOWER(album) LIKE LOWER(?))")
            params.extend([term, term, term])

        sql = f"SELECT * FROM tracks WHERE {' AND '.join(clauses)} LIMIT 20"
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
        try:
            return self.provider.search(query)
        except Exception:
            return []

class SearchOrchestrator:
    def __init__(
        self,
        local_service: LocalSearchService,
        remote_service: RemoteSearchService,
        sc_service: Optional[SoundCloudSearchService] = None
    ):
        self.local_service = local_service
        self.remote_service = remote_service
        self.sc_service = sc_service

    def _sync_and_map_remote(self, remote_results: List[Dict[str, Any]], local_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        local_ext_ids = {t.get("external_id") for t in local_results if t.get("external_id")}
        local_keys = {(t.get("title", "").strip().lower(), t.get("artist", "").strip().lower()) for t in local_results}

        conn = get_db()
        mapped_remote = []

        try:
            for r in remote_results:
                video_id = r.get("video_id") or r.get("videoId") or r.get("external_id")
                title = (r.get("title") or "").strip()
                artist = (r.get("artist") or "").strip()
                remote_cover = r.get("cover_path")
                remote_duration = r.get("duration")

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

                if db_track:
                    track_id = db_track["id"]
                    is_liked = bool(db_track.get("liked"))
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

                    if video_id in local_ext_ids or (title.lower(), artist.lower()) in local_keys:
                        continue

                    mapped_remote.append({
                        "id": track_id,
                        "path": db_track.get("path") or f"https://www.youtube.com/watch?v={video_id}",
                        "title": title,
                        "artist": artist,
                        "album": db_track.get("album") or r.get("album") or "-",
                        "genre": db_track.get("genre"),
                        "year": db_track.get("year"),
                        "track_number": db_track.get("track_number"),
                        "duration": new_dur,
                        "cover_path": new_cover or remote_cover,
                        "source": db_track.get("source") or "remote",
                        "external_id": video_id or db_track.get("external_id"),
                        "cache_path": db_track.get("cache_path"),
                        "liked": is_liked,
                        "last_accessed": db_track.get("last_accessed")
                    })
                else:
                    mapped_remote.append({
                        "id": -1,
                        "path": f"https://www.youtube.com/watch?v={video_id}",
                        "title": title,
                        "artist": artist,
                        "album": r.get("album") or "-",
                        "genre": None,
                        "year": None,
                        "track_number": None,
                        "duration": remote_duration,
                        "cover_path": remote_cover,
                        "source": "remote",
                        "external_id": video_id,
                        "cache_path": None,
                        "liked": False,
                        "last_accessed": None
                    })
        finally:
            conn.close()

        return mapped_remote

    def _attach_db_state(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ext_ids = [r["external_id"] for r in results if r.get("external_id")]
        if not ext_ids:
            return results
        conn = get_db()
        try:
            placeholders = ",".join("?" * len(ext_ids))
            rows = conn.execute(
                f"SELECT id, external_id, liked, cache_path FROM tracks WHERE external_id IN ({placeholders})",
                ext_ids,
            ).fetchall()
        finally:
            conn.close()
        known = {row["external_id"]: row for row in rows}
        for r in results:
            row = known.get(r.get("external_id"))
            if row:
                r["id"] = row["id"]
                r["liked"] = bool(row["liked"])
                r["cache_path"] = row["cache_path"]
        return results

    def search(self, query: str) -> List[Dict[str, Any]]:
        local_res = self.local_service.search(query)
        with ThreadPoolExecutor(max_workers=2) as pool:
            remote_future = pool.submit(self.remote_service.search, query)
            sc_future = pool.submit(self.sc_service._sync_search, query, 10) if self.sc_service else None
            remote_raw = remote_future.result()
            sc_res = sc_future.result() if sc_future else []

        mapped_remote = self._sync_and_map_remote(remote_raw, local_res)
        return local_res + mapped_remote + self._attach_db_state(sc_res)

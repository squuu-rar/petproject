import os
import time
import shutil
import yt_dlp
import asyncio
from pathlib import Path
from typing import Optional
from db import get_db

class CacheManager:
    def __init__(self, cache_dir: Path, max_size_bytes: int):
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_bytes
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_current_size(self) -> int:
        return sum(f.stat().st_size for f in self.cache_dir.rglob('*') if f.is_file())

    async def get_or_download(self, track_id: int, stream_url: str) -> Path:
        """Returns the path to the cached file, downloading it if necessary."""
        conn = get_db()
        track = conn.execute("SELECT cache_path, source FROM tracks WHERE id = ?", (track_id,)).fetchone()
        conn.close()

        if not track:
            raise ValueError("Track not found")

        # If already cached
        if track["cache_path"]:
            cached_file = Path(track["cache_path"])
            if cached_file.exists():
                self._update_access_time(track_id)
                return cached_file

        # Download new
        return await self._download_track(track_id, stream_url)

    def _update_access_time(self, track_id: int):
        conn = get_db()
        try:
            conn.execute("UPDATE tracks SET last_accessed = ? WHERE id = ?", (time.time(), track_id))
            conn.commit()
        finally:
            conn.close()

    async def _download_track(self, track_id: int, stream_url: str) -> Path:
        # Use a temporary name to avoid partial files in cache
        temp_file = self.cache_dir / f"temp_{track_id}_{int(time.time())}.tmp"
        final_file = self.cache_dir / f"cache_{track_id}.mp3"

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(temp_file),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }

        try:
            # Run yt-dlp in a thread to not block event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([stream_url]))

            if not temp_file.exists():
                raise RuntimeError("Download failed: temp file not created")

            # Rename temp to final
            if final_file.exists():
                final_file.unlink()
            temp_file.rename(final_file)

            # Update DB
            self._update_access_time(track_id)
            conn = get_db()
            try:
                conn.execute("UPDATE tracks SET cache_path = ? WHERE id = ?", (str(final_file.absolute()), track_id))
                conn.commit()
            finally:
                conn.close()

            self._evict_if_needed()
            return final_file

        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise RuntimeError(f"Download failed: {str(e)}")

    def _evict_if_needed(self):
        current_size = self._get_current_size()
        if current_size <= self.max_size_bytes:
            return

        conn = get_db()
        try:
            # Get oldest unliked files
            cur = conn.cursor()
            oldest_files = cur.execute("""
                SELECT id, cache_path FROM tracks 
                WHERE source = 'remote' 
                  AND liked = 0 
                  AND cache_path IS NOT NULL 
                  AND last_accessed > 0
                ORDER BY last_accessed ASC
            """).fetchall()

            for row in oldest_files:
                if self._get_current_size() <= self.max_size_bytes:
                    break
                
                track_id = row['id']
                path = Path(row['cache_path'])
                
                if path.exists():
                    path.unlink()
                
                conn.execute("UPDATE tracks SET cache_path = NULL WHERE id = ?", (track_id,))
            
            conn.commit()
        finally:
            conn.close()

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Dict, Optional

import yt_dlp

from db import get_db
from ytdlp_opts import download_opts

# Расширение файла должно соответствовать содержимому: раньше всё сохранялось как .mp3,
# хотя yt-dlp отдаёт webm/m4a, и браузер получал неверный Content-Type.
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}
PARTIAL_SUFFIXES = (".part", ".ytdl", ".tmp")
MIN_VALID_BYTES = 8 * 1024        # меньше — это не аудио, а страница с ошибкой
STALE_AFTER_S = 3600              # недокачанные файлы старше часа считаем мусором


class CacheManager:
    def __init__(self, cache_dir: Path, max_size_bytes: int):
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max_size_bytes
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # track_id -> задача загрузки. Браузер при воспроизведении шлёт несколько Range-запросов
        # к одному треку; без этого каждый запускал собственную загрузку того же файла.
        self._inflight: Dict[int, "asyncio.Task[Optional[Path]]"] = {}
        self._cleanup_stale()

    # ------------------------------------------------------------------ утилиты
    @staticmethod
    def media_type(path: Path | str) -> str:
        return MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")

    def _get_current_size(self) -> int:
        return sum(f.stat().st_size for f in self.cache_dir.rglob("*") if f.is_file())

    def _update_access_time(self, track_id: int) -> None:
        conn = get_db()
        try:
            conn.execute("UPDATE tracks SET last_accessed = ? WHERE id = ?", (time.time(), track_id))
            conn.commit()
        finally:
            conn.close()

    touch = _update_access_time

    def _cleanup_stale(self) -> None:
        """Убирает обрывки прошлых загрузок (.part/.tmp и старые temp_*), если процесс падал."""
        now = time.time()
        for f in self.cache_dir.iterdir():
            try:
                if not f.is_file():
                    continue
                stale_name = f.suffix in PARTIAL_SUFFIXES or f.name.startswith("temp_")
                if stale_name and now - f.stat().st_mtime > STALE_AFTER_S:
                    f.unlink()
            except OSError:
                pass

    def _load_track(self, track_id: int) -> Optional[dict]:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT cache_path, source, external_id, path FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    @staticmethod
    def _existing(cache_path: Optional[str]) -> Optional[Path]:
        if not cache_path:
            return None
        p = Path(cache_path)
        return p if p.is_file() and p.stat().st_size >= MIN_VALID_BYTES else None

    def get_cached_path(self, track_id: int) -> Optional[Path]:
        """Готовый файл из кэша (и отметка «недавно играл») либо None. Сеть не используется."""
        track = self._load_track(track_id)
        cached = self._existing(track.get("cache_path")) if track else None
        if cached:
            self._update_access_time(track_id)
        return cached

    # ------------------------------------------------------------------ выбор ссылки
    @staticmethod
    def _pick_url(track: dict, stream_url: str) -> str:
        source = track.get("source")
        ext_id = str(track.get("external_id") or "")
        path_val = str(track.get("path") or "")
        stream_url = str(stream_url or "")
        http = ("http://", "https://")

        # SoundCloud определяем и по ссылке: трек мог быть сохранён с source='remote'
        if source == "soundcloud" or "soundcloud.com" in ext_id or "soundcloud.com" in path_val:
            # Качаем с постоянной страницы трека: yt-dlp сам выберет прогрессивный формат.
            # Прямая ссылка из stream_url нередко ведёт на HLS-манифест и протухает.
            for candidate in (ext_id, path_val, stream_url):
                if candidate.startswith(http):
                    return candidate
            return ext_id or path_val

        # YouTube
        if ext_id.startswith(http):
            return ext_id
        if ext_id:
            return f"https://www.youtube.com/watch?v={ext_id}"
        if "youtube.com" in stream_url or "youtu.be" in stream_url:
            return stream_url
        return stream_url or path_val

    # ------------------------------------------------------------------ загрузка
    async def get_or_download(self, track_id: int, stream_url: str = "") -> Optional[Path]:
        track = self._load_track(track_id)
        if not track:
            return None

        cached = self._existing(track.get("cache_path"))
        if cached:
            self._update_access_time(track_id)
            return cached

        task = self._inflight.get(track_id)
        if task is None:
            url = self._pick_url(track, stream_url)
            task = asyncio.ensure_future(self._download_track(track_id, url))
            self._inflight[track_id] = task

            def _forget(done, tid=track_id):
                if self._inflight.get(tid) is done:
                    self._inflight.pop(tid, None)

            task.add_done_callback(_forget)

        # shield: если запрос клиента оборвался, загрузка должна дойти до конца и лечь в кэш
        return await asyncio.shield(task)

    async def ensure_cached(self, track_id: int, stream_url: str = "", timeout: float = 60.0) -> Optional[Path]:
        """Ждёт файл не дольше timeout. По таймауту отдаёт None, а загрузка продолжается в фоне."""
        try:
            return await asyncio.wait_for(self.get_or_download(track_id, stream_url), timeout)
        except asyncio.TimeoutError:
            return None

    async def _download_track(self, track_id: int, url: str) -> Optional[Path]:
        if not url:
            print(f"[CacheManager] Нет ссылки для трека {track_id}")
            return None
        try:
            path = await asyncio.to_thread(self._download_sync, track_id, url)
            if path is None:
                return None
            await asyncio.to_thread(self._finalize, track_id, path)
            return path
        except Exception as e:
            print(f"[CacheManager] Загрузка пропущена для трека {track_id}: {e}")
            return None

    def _remove_variants(self, track_id: int) -> None:
        for f in self.cache_dir.glob(f"cache_{track_id}.*"):
            try:
                f.unlink()
            except OSError:
                pass

    def _download_sync(self, track_id: int, url: str) -> Optional[Path]:
        self._remove_variants(track_id)
        outtmpl = str(self.cache_dir / f"cache_{track_id}.%(ext)s")
        try:
            with yt_dlp.YoutubeDL(download_opts(outtmpl)) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception:
            self._remove_variants(track_id)
            raise

        path = self._find_downloaded(track_id, info)
        if path is None or path.stat().st_size < MIN_VALID_BYTES:
            self._remove_variants(track_id)
            return None
        return path

    def _find_downloaded(self, track_id: int, info: Optional[dict]) -> Optional[Path]:
        for item in (info or {}).get("requested_downloads") or []:
            fp = item.get("filepath") or item.get("_filename")
            if fp and Path(fp).is_file():
                return Path(fp)
        candidates = [
            f for f in self.cache_dir.glob(f"cache_{track_id}.*")
            if f.is_file() and f.suffix not in PARTIAL_SUFFIXES
        ]
        return max(candidates, key=lambda f: f.stat().st_mtime) if candidates else None

    def _finalize(self, track_id: int, path: Path) -> None:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE tracks SET cache_path = ?, last_accessed = ? WHERE id = ?",
                (str(path.absolute()), time.time(), track_id),
            )
            conn.commit()
        finally:
            conn.close()
        self._evict_if_needed(keep_track_id=track_id)

    # ------------------------------------------------------------------ вытеснение
    def _evict_if_needed(self, keep_track_id: Optional[int] = None) -> None:
        total = self._get_current_size()
        if total <= self.max_size_bytes:
            return

        conn = get_db()
        try:
            # Любые не-локальные треки (YouTube и SoundCloud), кроме лайкнутых. Раньше условие
            # source = 'remote' пропускало SoundCloud, и его кэш рос без ограничений.
            rows = conn.execute(
                """
                SELECT id, cache_path FROM tracks
                WHERE COALESCE(source, 'local') != 'local'
                  AND COALESCE(liked, 0) = 0
                  AND cache_path IS NOT NULL
                ORDER BY COALESCE(last_accessed, 0) ASC
                """
            ).fetchall()

            for row in rows:
                if total <= self.max_size_bytes:
                    break
                if keep_track_id is not None and row["id"] == keep_track_id:
                    continue  # только что скачанный трек не выкидываем сразу же
                p = Path(row["cache_path"])
                size = p.stat().st_size if p.is_file() else 0
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    continue
                total -= size
                conn.execute("UPDATE tracks SET cache_path = NULL WHERE id = ?", (row["id"],))
            conn.commit()
        finally:
            conn.close()

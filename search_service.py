from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ytmusicapi import YTMusic

from db import get_db
from providers import RemoteDiscoveryProvider
from soundcloud_service import SoundCloudSearchService, is_soundcloud_url

# Общий бюджет времени на онлайн-поиск (YouTube Music + SoundCloud параллельно).
# Раньше медленный источник мог держать ответ минутами.
SEARCH_TIMEOUT_S = float(os.environ.get("SEARCH_TIMEOUT_S", "15"))


def _pylower(value):
    return value.lower() if isinstance(value, str) else value


def _open_db():
    """Соединение с функцией PYLOWER.

    LOWER() и LIKE в SQLite нечувствительны к регистру только для ASCII: «пикник» не находил
    «Пикник». PYLOWER использует Python str.lower(), который понимает кириллицу."""
    conn = get_db()
    conn.create_function("PYLOWER", 1, _pylower, deterministic=True)
    return conn


def _escape_like(text: str) -> str:
    """% и _ из запроса пользователя не должны работать как шаблоны LIKE."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _fix_source(track: Dict[str, Any]) -> Dict[str, Any]:
    """Трек SoundCloud мог быть сохранён в БД с source='remote'. Определяем по ссылке,
    иначе в выдаче он попадает в группу «YouTube Music» и не сопоставляется со свежим."""
    if track.get("source") in (None, "", "remote") and (
        is_soundcloud_url(track.get("external_id")) or is_soundcloud_url(track.get("path"))
    ):
        track["source"] = "soundcloud"
    return track


def _source_group(track: Dict[str, Any]) -> str:
    source = track.get("source") or "local"
    if source == "local":
        return "local"
    if source == "soundcloud" or is_soundcloud_url(track.get("external_id")):
        return "soundcloud"
    return "remote"


class LocalSearchService:
    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        words = query.strip().split()
        if not words:
            return []

        clauses = []
        params: List[Any] = []
        for word in words:
            term = f"%{_escape_like(word.lower())}%"
            clauses.append(
                "(PYLOWER(title) LIKE ? ESCAPE '\\' "
                "OR PYLOWER(artist) LIKE ? ESCAPE '\\' "
                "OR PYLOWER(album) LIKE ? ESCAPE '\\')"
            )
            params.extend([term, term, term])

        # локальные файлы первыми, затем лайкнутые: иначе 20 мест займут сохранённые онлайн-треки
        sql = (
            f"SELECT * FROM tracks WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE COALESCE(source, 'local') WHEN 'local' THEN 0 ELSE 1 END, "
            "COALESCE(liked, 0) DESC, id DESC LIMIT ?"
        )
        params.append(limit)
        conn = _open_db()
        try:
            return [_fix_source(dict(row)) for row in conn.execute(sql, params).fetchall()]
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
        sc_service: Optional[SoundCloudSearchService] = None,
    ):
        self.local_service = local_service
        self.remote_service = remote_service
        self.sc_service = sc_service

    # ------------------------------------------------------------------ YouTube Music
    def _sync_and_map_remote(
        self, remote_results: List[Dict[str, Any]], local_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        local_ext_ids = {t.get("external_id") for t in local_results if t.get("external_id")}
        # `or ""`: у треков без тега артиста в БД NULL, и .get("artist", "") вернул бы None ->
        # AttributeError -> весь поиск отвечал 500
        local_keys = {(_norm(t.get("title")), _norm(t.get("artist"))) for t in local_results}

        conn = _open_db()
        mapped_remote: List[Dict[str, Any]] = []
        seen_video_ids = set()

        try:
            for r in remote_results:
                video_id = r.get("video_id") or r.get("videoId") or r.get("external_id")
                if video_id:
                    if video_id in seen_video_ids:
                        continue
                    seen_video_ids.add(video_id)

                title = (r.get("title") or "").strip()
                artist = (r.get("artist") or "").strip()
                remote_cover = r.get("cover_path")
                remote_duration = r.get("duration")

                db_track = None
                if video_id:
                    row = conn.execute("SELECT * FROM tracks WHERE external_id = ?", (video_id,)).fetchone()
                    if row:
                        db_track = dict(row)

                # Совпадение по названию и артисту — только с локальными и YouTube-треками.
                # С SoundCloud-записью оно подменяло YouTube-результат SoundCloud-треком,
                # и тот потом дублировался в выдаче SoundCloud.
                if not db_track and title and artist:
                    row = conn.execute(
                        "SELECT * FROM tracks WHERE PYLOWER(title) = PYLOWER(?) AND PYLOWER(artist) = PYLOWER(?) "
                        "AND COALESCE(source, 'local') != 'soundcloud' AND COALESCE(external_id, '') NOT LIKE '%soundcloud.com%' "
                        "LIMIT 1",
                        (title, artist),
                    ).fetchone()
                    if row:
                        db_track = dict(row)

                if db_track:
                    track_id = db_track["id"]
                    new_cover = db_track.get("cover_path")
                    new_dur = db_track.get("duration")
                    need_update = False

                    if not new_cover and remote_cover:
                        new_cover = remote_cover
                        need_update = True
                    if not new_dur and remote_duration:
                        new_dur = remote_duration
                        need_update = True

                    if need_update:
                        conn.execute(
                            "UPDATE tracks SET cover_path = ?, duration = ? WHERE id = ?",
                            (new_cover, new_dur, track_id),
                        )
                        conn.commit()

                    if video_id in local_ext_ids or (_norm(title), _norm(artist)) in local_keys:
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
                        "liked": bool(db_track.get("liked")),
                        "last_accessed": db_track.get("last_accessed"),
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
                        "last_accessed": None,
                    })
        finally:
            conn.close()

        return mapped_remote

    # ------------------------------------------------------------------ SoundCloud
    def _attach_db_state(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ext_ids = [r["external_id"] for r in results if r.get("external_id")]
        if not ext_ids:
            return results
        conn = _open_db()
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
            r["source"] = "soundcloud"
            row = known.get(r.get("external_id"))
            if row:
                r["id"] = row["id"]
                r["liked"] = bool(row["liked"])
                r["cache_path"] = row["cache_path"]
        return results

    # ------------------------------------------------------------------ сборка
    @staticmethod
    def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Один трек — одна строка. Порядок сохраняется: побеждает тот, что встретился раньше
        (локальные, затем YouTube Music, затем SoundCloud).

        SoundCloud дублировался так: поиск по БД отдаёт уже сохранённый трек, а свежий
        поиск SoundCloud отдаёт тот же трек ещё раз — и обе копии шли в выдачу."""
        seen_ids, seen_ext, seen_names = set(), set(), set()
        unique: List[Dict[str, Any]] = []
        for t in items:
            track_id = t.get("id")
            ext = t.get("external_id")
            names = (_source_group(t), _norm(t.get("title")), _norm(t.get("artist")))

            if isinstance(track_id, int) and track_id > 0 and track_id in seen_ids:
                continue
            if ext and ext in seen_ext:
                continue
            if names[1] and names[2] and names in seen_names:
                continue

            if isinstance(track_id, int) and track_id > 0:
                seen_ids.add(track_id)
            if ext:
                seen_ext.add(ext)
            seen_names.add(names)
            unique.append(t)
        return unique

    @staticmethod
    def _result(future, deadline: float, label: str) -> List[Dict[str, Any]]:
        if future is None:
            return []
        try:
            return future.result(timeout=max(0.05, deadline - time.monotonic())) or []
        except Exception as e:  # таймаут или сбой источника: остальные результаты всё равно отдаём
            print(f"[search] {label}: пропущен ({type(e).__name__})")
            return []

    def search(self, query: str) -> List[Dict[str, Any]]:
        local_res = self.local_service.search(query)

        # без `with`: выход из него ждал бы зависшие потоки и сводил таймаут на нет
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            deadline = time.monotonic() + SEARCH_TIMEOUT_S
            remote_future = pool.submit(self.remote_service.search, query)
            sc_future = pool.submit(self.sc_service._sync_search, query, 10) if self.sc_service else None
            remote_raw = self._result(remote_future, deadline, "YouTube Music")
            sc_raw = self._result(sc_future, deadline, "SoundCloud")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        mapped_remote = self._sync_and_map_remote(remote_raw, local_res)
        sc_res = self._attach_db_state(sc_raw)
        return self._dedupe(local_res + mapped_remote + sc_res)

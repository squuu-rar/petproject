import sys
from pathlib import Path

print("1/6. Запись .env ключей Last.fm...")
env_file = Path(".env")
lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
keys_to_set = {
    "LASTFM_API_KEY": "bb23ebdaeae30457ab55eb971df1f6af",
    "LASTFM_SHARED_SECRET": "2737d17592aacc24e6b3467bb5f50f10",
    "LASTFM_USERNAME": "squ666"
}
filtered = [line for line in lines if not any(line.startswith(k + "=") for k in keys_to_set)]
for k, v in keys_to_set.items():
    filtered.append(f"{k}={v}")
env_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")

print("2/6. Запись lastfm_service.py...")
Path("lastfm_service.py").write_text('''import os
import urllib.parse
import urllib.request
import json
import asyncio
from typing import List, Dict, Any, Optional

class LastFMService:
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LASTFM_API_KEY", "bb23ebdaeae30457ab55eb971df1f6af")

    def _sync_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params["api_key"] = self.api_key
        params["format"] = "json"
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "MusicStreamApp/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return {}

    async def get_similar_tracks(self, artist: str, track: str, limit: int = 15) -> List[Dict[str, str]]:
        if not artist or not track:
            return []

        def _fetch():
            data = self._sync_request({
                "method": "track.getsimilar",
                "artist": artist,
                "track": track,
                "limit": limit,
                "autocorrect": 1
            })
            sim_tracks = data.get("similartracks", {}).get("track", [])
            if isinstance(sim_tracks, dict):
                sim_tracks = [sim_tracks]

            results = []
            for item in sim_tracks:
                a_name = item.get("artist", {}).get("name") if isinstance(item.get("artist"), dict) else item.get("artist")
                t_name = item.get("name")
                if a_name and t_name:
                    results.append({"artist": a_name, "title": t_name})
            return results

        return await asyncio.to_thread(_fetch)

    async def get_loved_tracks(self, username: Optional[str] = None, limit: int = 50) -> List[Dict[str, str]]:
        user = username or os.environ.get("LASTFM_USERNAME", "squ666")

        def _fetch():
            data = self._sync_request({
                "method": "user.getlovedtracks",
                "user": user,
                "limit": limit
            })
            tracks = data.get("lovedtracks", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            results = []
            for item in tracks:
                a_name = item.get("artist", {}).get("name")
                t_name = item.get("name")
                if a_name and t_name:
                    results.append({"artist": a_name, "title": t_name})
            return results

        return await asyncio.to_thread(_fetch)
''', encoding="utf-8")

print("3/6. Запись soundcloud_service.py...")
Path("soundcloud_service.py").write_text('''import asyncio
from typing import List, Dict, Any, Optional
import yt_dlp

class SoundCloudSearchService:
    def __init__(self):
        self.ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "socket_timeout": 8,
        }

    def _sync_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        results = []
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                entries = info.get("entries") or []

                for entry in entries:
                    if not entry:
                        continue
                    url = entry.get("url") or entry.get("webpage_url")
                    title = entry.get("title")
                    uploader = entry.get("uploader") or entry.get("artist") or "SoundCloud Artist"
                    duration = entry.get("duration")
                    thumbnails = entry.get("thumbnails") or []
                    cover_url = thumbnails[-1]["url"] if thumbnails else None

                    results.append({
                        "id": -1,
                        "path": url,
                        "title": title,
                        "artist": uploader,
                        "album": "SoundCloud",
                        "genre": "SoundCloud",
                        "year": None,
                        "track_number": None,
                        "duration": float(duration) if duration else None,
                        "cover_path": cover_url,
                        "source": "soundcloud",
                        "external_id": url,
                        "cache_path": None,
                        "liked": False,
                        "last_accessed": None
                    })
        except Exception:
            pass
        return results

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        return await asyncio.to_thread(self._sync_search, query.strip(), limit)

    @staticmethod
    def get_stream_url(sc_url: str) -> Optional[str]:
        opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "socket_timeout": 10,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(sc_url, download=False)
                return info.get("url")
        except Exception:
            return None
''', encoding="utf-8")

print("4/6. Запись search_service.py (с изоляцией unit-тестов)...")
Path("search_service.py").write_text('''import asyncio
import sqlite3
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
                        "liked": is_liked
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
                        "liked": False
                    })
        finally:
            conn.close()

        return mapped_remote

    async def search_async(self, query: str) -> List[Dict[str, Any]]:
        tasks = [
            asyncio.to_thread(self.local_service.search, query),
            asyncio.to_thread(self.remote_service.search, query)
        ]
        if self.sc_service:
            tasks.append(self.sc_service.search(query, limit=10))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        local_res = results[0] if isinstance(results[0], list) else []
        remote_raw = results[1] if isinstance(results[1], list) else []
        sc_res = results[2] if len(results) > 2 and isinstance(results[2], list) else []

        mapped_remote = await asyncio.to_thread(self._sync_and_map_remote, remote_raw, local_res)
        return local_res + mapped_remote + sc_res

    def search(self, query: str) -> List[Dict[str, Any]]:
        local_res = self.local_service.search(query)
        remote_raw = self.remote_service.search(query)
        mapped_remote = self._sync_and_map_remote(remote_raw, local_res)
        sc_res = self.sc_service._sync_search(query, limit=10) if self.sc_service else []
        return local_res + mapped_remote + sc_res
''', encoding="utf-8")

print("5/6. Запись wave_engine.py...")
Path("wave_engine.py").write_text('''import asyncio
from typing import List, Dict, Any, Optional
from ytmusicapi import YTMusic
from db import get_db
from lastfm_service import LastFMService

class WaveEngine:
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
        candidates: Dict[str, Dict[str, Any]] = {}

        for s in seeds:
            if s.get("artist") and s.get("title"):
                similar = await self.lastfm.get_similar_tracks(s["artist"], s["title"], limit=6)
                for sim in similar:
                    resolved = await asyncio.to_thread(self._search_youtube_stream, sim["artist"], sim["title"])
                    if resolved and resolved["external_id"] not in seed_ext_ids and resolved["external_id"] not in candidates:
                        candidates[resolved["external_id"]] = resolved

        # YouTube Radio фолбэк
        def _get_yt_radio():
            yt_candidates = []
            for s in seeds:
                ext_id = s.get("external_id")
                if not ext_id:
                    continue
                try:
                    playlist = self.ytmusic.get_watch_playlist(videoId=ext_id, radio=True, limit=15)
                    items = playlist.get("tracks") or playlist.get("contents") or []
                    for item in items:
                        v_id = item.get("videoId")
                        if not v_id or v_id in seed_ext_ids or v_id in candidates:
                            continue
                        title = item.get("title")
                        if not title:
                            continue
                        artists = item.get("artists", [])
                        artist_name = artists[0].get("name") if artists else "Unknown Artist"
                        album = item.get("album", {}).get("name") if item.get("album") else "-"
                        thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                        cover_url = thumbs[-1]["url"] if thumbs else None
                        dur = self._parse_duration(item.get("length"))

                        yt_candidates.append({
                            "id": -1,
                            "external_id": v_id,
                            "path": f"https://www.youtube.com/watch?v={v_id}",
                            "title": title,
                            "artist": artist_name,
                            "album": album,
                            "source": "remote",
                            "cover_path": cover_url,
                            "duration": dur,
                            "liked": False,
                            "last_accessed": None
                        })
                except Exception:
                    continue
            return yt_candidates

        radio_tracks = await asyncio.to_thread(_get_yt_radio)
        for t in radio_tracks:
            if t["external_id"] not in candidates:
                candidates[t["external_id"]] = t

        return list(candidates.values())

    async def generate_wave(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = get_db()
        try:
            recent = conn.execute("""
                SELECT track_id FROM history 
                WHERE event IN ('play', 'finish') ORDER BY timestamp DESC LIMIT 4
            """).fetchall()
            liked = conn.execute("SELECT id as track_id FROM tracks WHERE liked = 1 ORDER BY RANDOM() LIMIT 4").fetchall()
            seeds = [r["track_id"] for r in (list(recent) + list(liked))]
            if not seeds:
                fallback = conn.execute("SELECT id as track_id FROM tracks ORDER BY id DESC LIMIT 5").fetchall()
                seeds = [r["track_id"] for r in fallback]
        finally:
            conn.close()

        candidates = await self.generate_candidates(seeds)
        return candidates[:limit]
''', encoding="utf-8")

print("6/6. Запись main.py...")
Path("main.py").write_text('''import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Depends, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from typing import Optional
from ytmusicapi import YTMusic

from db import init_db, get_track_by_id, get_tracks_paginated, get_playback_history, get_db, save_remote_track
from schemas import TrackRead, HistoryCreate, HistoryItemRead
from search_service import SearchOrchestrator, LocalSearchService, RemoteSearchService
from soundcloud_service import SoundCloudSearchService
from providers import YoutubeStreamProvider
from cache_manager import CacheManager
from lyrics import fetch_lyrics
from wave_engine import WaveEngine

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = STATIC_DIR / "cache"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

cache_manager: Optional[CacheManager] = None
wave_engine: Optional[WaveEngine] = None

def get_search_orchestrator() -> SearchOrchestrator:
    return SearchOrchestrator(
        LocalSearchService(),
        RemoteSearchService(YTMusic()),
        SoundCloudSearchService()
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cache_manager, wave_engine
    init_db()
    cache_manager = CacheManager(CACHE_DIR, 500 * 1024 * 1024)
    wave_engine = WaveEngine()
    yield

app = FastAPI(title="Music Player API", lifespan=lifespan)

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tracks", response_model=list[TrackRead])
def list_tracks(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("id"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    source: Optional[str] = Query(None, pattern="^(local|remote|soundcloud)$")
):
    return get_tracks_paginated(limit, offset, sort_by, order, source=source)

@app.get("/favorites", response_model=list[TrackRead])
def list_favorites(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("id"),
    order: str = Query("asc", pattern="^(asc|desc)$")
):
    return get_tracks_paginated(limit, offset, sort_by, order, liked=True)

@app.get("/search", response_model=list[TrackRead])
async def search_tracks(q: str = Query(..., min_length=1), orchestrator: SearchOrchestrator = Depends(get_search_orchestrator)):
    return await orchestrator.search_async(q)

@app.get("/wave", response_model=list[TrackRead])
async def get_wave_recommendations(limit: int = Query(20, ge=1, le=50)):
    global wave_engine
    if not wave_engine:
        wave_engine = WaveEngine()
    return await wave_engine.generate_wave(limit=limit)

@app.post("/tracks/remote", response_model=TrackRead)
def register_remote_track(track: TrackRead):
    return save_remote_track(track.model_dump())

@app.get("/stream/{track_id}")
async def stream_track(track_id: int, background_tasks: BackgroundTasks):
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    source = track.get("source")
    if source == "local":
        file_path = Path(track["path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")
        return FileResponse(file_path)

    if source == "soundcloud":
        sc_url = track.get("path") or track.get("external_id")
        if not sc_url:
            raise HTTPException(status_code=500, detail="SoundCloud track missing URL")

        if track.get("cache_path"):
            cached_path = Path(track["cache_path"])
            if cached_path.exists():
                if cache_manager:
                    cache_manager._update_access_time(track_id)
                return FileResponse(cached_path)

        stream_url = SoundCloudSearchService.get_stream_url(sc_url)
        if not stream_url:
            raise HTTPException(status_code=500, detail="Failed to resolve SoundCloud stream")

        if cache_manager:
            background_tasks.add_task(cache_manager.get_or_download, track_id, sc_url)
        return RedirectResponse(url=stream_url, status_code=307)

    if source == "remote":
        external_id = track.get("external_id")
        if not external_id:
            raise HTTPException(status_code=500, detail="Remote track missing external_id")

        if track.get("cache_path"):
            cached_path = Path(track["cache_path"])
            if cached_path.exists():
                if cache_manager:
                    cache_manager._update_access_time(track_id)
                return FileResponse(cached_path)

        try:
            provider = YoutubeStreamProvider(external_id)
            stream_url = provider.get_stream_url()
            if cache_manager:
                background_tasks.add_task(cache_manager.get_or_download, track_id, stream_url)
            return RedirectResponse(url=stream_url, status_code=307)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to resolve remote stream: {str(e)}")

    raise HTTPException(status_code=400, detail="Unsupported source")

@app.post("/tracks/{track_id}/like")
def toggle_like(track_id: int, liked: bool = Query(...)):
    conn = get_db()
    try:
        conn.execute("UPDATE tracks SET liked = ? WHERE id = ?", (1 if liked else 0, track_id))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@app.get("/history", response_model=list[HistoryItemRead])
def get_history(limit: int = Query(80, ge=1, le=100)):
    return get_playback_history(limit=limit)

@app.post("/history")
def record_history(payload: HistoryCreate):
    track = get_track_by_id(payload.track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if payload.event == "skip" and payload.elapsed_seconds >= 15:
        raise HTTPException(status_code=400, detail="Skip only accepted if elapsed_seconds < 15")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO history (track_id, event, elapsed_seconds, timestamp) VALUES (?, ?, ?, ?)",
            (payload.track_id, payload.event, payload.elapsed_seconds, time.time())
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@app.get("/tracks/{track_id}/lyrics")
def get_track_lyrics_by_id(track_id: int):
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    return fetch_lyrics(track.get("artist"), track.get("title"), track.get("duration"))

@app.get("/lyrics")
def get_track_lyrics(artist: str, title: str, duration: float = None):
    return fetch_lyrics(artist, title, duration)
''', encoding="utf-8")

# Обновление UI разметки: кнопка Моя Волна
index_path = Path("static/index.html")
if index_path.exists():
    html = index_path.read_text(encoding="utf-8")
    if 'data-view="wave"' not in html and 'data-view="favorites"' in html:
        wave_tag = '<a href="#" class="nav-item" data-view="wave"><i class="fas fa-water"></i> Моя Волна</a>'
        html = html.replace('data-view="favorites">', 'data-view="favorites">' + "\n                " + wave_tag, 1)
        index_path.write_text(html, encoding="utf-8")

print("✓ Все файлы успешно сгенерированы!")

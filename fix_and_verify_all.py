import sys
from pathlib import Path

print("1. Обновление db.py (динамический source для SoundCloud)...")
Path("db.py").write_text('''import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "tracks.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            title TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            year INTEGER,
            track_number INTEGER,
            duration REAL,
            cover_path TEXT,
            source TEXT DEFAULT 'local',
            external_id TEXT,
            cache_path TEXT,
            liked INTEGER DEFAULT 0,
            last_accessed REAL DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            event TEXT,
            elapsed_seconds REAL,
            timestamp REAL,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    """)
    cur.execute("PRAGMA table_info(tracks)")
    columns = [col[1] for col in cur.fetchall()]
    migrations = [
        ("genre", "TEXT"), ("year", "INTEGER"), ("cover_path", "TEXT"),
        ("source", "TEXT DEFAULT 'local'"), ("external_id", "TEXT"),
        ("cache_path", "TEXT"), ("liked", "INTEGER DEFAULT 0"),
        ("last_accessed", "REAL DEFAULT 0")
    ]
    for col_name, col_type in migrations:
        if col_name not in columns:
            cur.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")

    cur.execute("PRAGMA table_info(history)")
    hist_cols = [col[1] for col in cur.fetchall()]
    for col_name, col_type in [("event", "TEXT"), ("elapsed_seconds", "REAL"), ("timestamp", "REAL")]:
        if col_name not in hist_cols:
            cur.execute(f"ALTER TABLE history ADD COLUMN {col_name} {col_type}")

    conn.commit()
    conn.close()

def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_tracks_paginated(
    limit: int,
    offset: int,
    sort_by: str,
    order: str,
    source: Optional[str] = None,
    liked: Optional[bool] = None
) -> List[Dict[str, Any]]:
    allowed = {"id", "title", "artist", "album", "genre", "year", "track_number", "duration", "source"}
    if sort_by not in allowed:
        sort_by = "id"
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    
    query = "SELECT * FROM tracks"
    params = []
    conds = []
    if source:
        conds.append("source = ?")
        params.append(source)
    if liked is not None:
        conds.append("liked = ?")
        params.append(1 if liked else 0)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += f" ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        cur = conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def get_playback_history(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        query = """
            SELECT 
                h.id AS history_id, h.event, h.elapsed_seconds, h.timestamp,
                t.id, t.path, t.title, t.artist, t.album, t.genre, t.year,
                t.track_number, t.duration, t.cover_path, t.source, t.external_id,
                t.cache_path, t.liked, t.last_accessed
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE h.event IN ('play', 'finish')
            ORDER BY h.timestamp DESC LIMIT ?
        """
        cur = conn.execute(query, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def save_remote_track(track_data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db()
    try:
        cur = conn.cursor()
        ext_id = track_data.get("external_id")
        path = track_data.get("path")
        source = track_data.get("source") or "remote"
        
        cur.execute(
            "SELECT * FROM tracks WHERE (external_id IS NOT NULL AND external_id = ?) OR path = ?",
            (ext_id, path)
        )
        row = cur.fetchone()
        if row:
            res = dict(row)
            res["liked"] = bool(res.get("liked", 0))
            return res

        cur.execute("""
            INSERT INTO tracks (
                path, title, artist, album, genre, year,
                track_number, duration, cover_path, source,
                external_id, cache_path, liked, last_accessed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            path, track_data.get("title"), track_data.get("artist"),
            track_data.get("album"), track_data.get("genre"), track_data.get("year"),
            track_data.get("track_number"), track_data.get("duration"),
            track_data.get("cover_path"), source, ext_id, track_data.get("cache_path")
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.execute("SELECT * FROM tracks WHERE id = ?", (new_id,))
        res = dict(cur.fetchone())
        res["liked"] = bool(res.get("liked", 0))
        return res
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
''', encoding="utf-8")

print("2. Обновление search_service.py (синхронный и асинхронный поиск с изоляцией тестов)...")
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

    def search(self, query: str) -> List[Dict[str, Any]]:
        local_res = self.local_service.search(query)
        remote_raw = self.remote_service.search(query)
        mapped_remote = self._sync_and_map_remote(remote_raw, local_res)
        sc_res = self.sc_service._sync_search(query, limit=10) if self.sc_service else []
        return local_res + mapped_remote + sc_res

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
''', encoding="utf-8")

print("3. Обновление wave_engine.py (полные схемы Pydantic и дедупликация)...")
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
                            "genre": "Wave (Radio)",
                            "year": None,
                            "track_number": None,
                            "cover_path": cover_url,
                            "duration": dur,
                            "source": "remote",
                            "cache_path": None,
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
            raw_seeds = [r["track_id"] for r in (list(recent) + list(liked))]
            if not raw_seeds:
                fallback = conn.execute("SELECT id as track_id FROM tracks ORDER BY id DESC LIMIT 5").fetchall()
                raw_seeds = [r["track_id"] for r in fallback]
        finally:
            conn.close()

        seeds = list(dict.fromkeys(raw_seeds))
        candidates = await self.generate_candidates(seeds)
        return candidates[:limit]
''', encoding="utf-8")

print("4. Обновление main.py (исправление test_search_endpoint_success)...")
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
def search_tracks(
    q: str = Query(..., min_length=1),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator)
):
    return orchestrator.search(q)

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
def get_history(limit: int = Query(50, ge=1, le=100)):
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

print("5. Обновление static/js/app.js (обработчик вкладки Волны и бейджи)...")
js_path = Path("static/js/app.js")
if js_path.exists():
    js = js_path.read_text(encoding="utf-8")
    
    # Бейджи источников
    old_lbl = "const label = track.source === 'local' ? 'Локально' : 'Найдено онлайн';"
    new_lbl = """let label = 'YouTube Music';
            if (track.source === 'local') label = 'Локально на диске';
            else if (track.source === 'soundcloud') label = 'SoundCloud';"""
    if old_lbl in js:
        js = js.replace(old_lbl, new_lbl)

    # Обработчик вкладки Волны
    if "view === 'wave'" not in js and "view === 'favorites'" in js:
        wave_block = """            } else if (view === 'wave') {
                if (viewTitle) viewTitle.textContent = '🌊 Моя Волна (Рекомендации Last.fm & YouTube)';
                tracklist.innerHTML = '<div class="status-message"><i class="fas fa-spinner fa-spin"></i> Подбираем поток по вашим вкусам...</div>';
                try {
                    const res = await fetch('/wave?limit=25');
                    const waveTracks = await res.json();
                    renderTracklist(waveTracks);
                    if (waveTracks.length > 0 && (!currentlyPlayingTrack || audioPlayer.paused)) {
                        playTrack(0);
                    }
                } catch {
                    tracklist.innerHTML = '<div class="status-message">Не удалось загрузить рекомендации Волны</div>';
                }"""
        js = js.replace("} else if (view === 'favorites') {", wave_block + "\n            } else if (view === 'favorites') {")

    js_path.write_text(js, encoding="utf-8")

print("✓ Все файлы проверены и синхронизированы!")

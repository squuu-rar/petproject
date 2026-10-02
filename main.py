import time
from contextlib import asynccontextmanager
from functools import lru_cache
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

@lru_cache(maxsize=1)
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
    return FileResponse(STATIC_DIR / "index.html")

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
def stream_track(track_id: int, background_tasks: BackgroundTasks):
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
            if not stream_url:
                raise HTTPException(status_code=500, detail="Failed to extract YouTube stream URL")
            if cache_manager:
                yt_watch_url = f"https://www.youtube.com/watch?v={external_id}"
                background_tasks.add_task(cache_manager.get_or_download, track_id, yt_watch_url)
            return RedirectResponse(url=stream_url, status_code=307)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to resolve remote stream: {str(e)}")

    raise HTTPException(status_code=400, detail="Unsupported source")

@app.post("/tracks/{track_id}/like")
def toggle_like(track_id: int, liked: bool = Query(...)):
    conn = get_db()
    try:
        cur = conn.execute("UPDATE tracks SET liked = ? WHERE id = ?", (1 if liked else 0, track_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Track not found")
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

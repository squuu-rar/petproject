import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from typing import Optional
from ytmusicapi import YTMusic

from db import init_db, get_track_by_id, get_tracks_paginated
from schemas import TrackRead
from search_service import (
    SearchOrchestrator, 
    LocalSearchService, 
    RemoteSearchService
)
from providers import YoutubeStreamProvider
from cache_manager import CacheManager

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = STATIC_DIR / "cache"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Global cache manager instance
cache_manager: Optional[CacheManager] = None

# Dependency Injection setup
def get_search_orchestrator() -> SearchOrchestrator:
    ytmusic = YTMusic() 
    local_svc = LocalSearchService()
    remote_svc = RemoteSearchService(ytmusic)
    return SearchOrchestrator(local_svc, remote_svc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cache_manager
    init_db()
    # 500MB limit for cache
    cache_manager = CacheManager(CACHE_DIR, 500 * 1024 * 1024)
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
    source: Optional[str] = Query(None, pattern="^(local|remote)$")
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
    q: str = Query(..., min_length=1, description="Search query"),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator)
):
    return orchestrator.search(q)


@app.post("/tracks/remote", response_model=TrackRead)
def register_remote_track(track: TrackRead):
    from db import save_remote_track
    return save_remote_track(track.model_dump())

@app.get("/stream/{track_id}")
async def stream_track(track_id: int):
    track = get_track_by_id(track_id)
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    source = track.get("source")

    if source == "local":
        file_path = Path(track["path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")
        return FileResponse(file_path)

    if source == "remote":
        external_id = track.get("external_id")
        if not external_id:
            raise HTTPException(status_code=500, detail="Remote track missing external_id")

        if track.get("cache_path"):
            cached_path = Path(track["cache_path"])
            if cached_path.exists():
                return FileResponse(cached_path)

        try:
            provider = YoutubeStreamProvider(external_id)
            stream_url = provider.get_stream_url()
            return RedirectResponse(url=stream_url, status_code=307)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to resolve remote stream: {str(e)}")
    raise HTTPException(status_code=400, detail="Unsupported source")

@app.post("/tracks/{track_id}/like")
def toggle_like(track_id: int, liked: bool = Query(...)):
    from db import get_db
    import sqlite3
    conn = get_db()
    try:
        conn.execute("UPDATE tracks SET liked = ? WHERE id = ?", (1 if liked else 0, track_id))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

from lyrics import fetch_lyrics

@app.get("/lyrics")
def get_track_lyrics(artist: str, title: str, duration: float = None):
    return fetch_lyrics(artist, title, duration)

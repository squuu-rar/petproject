from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from typing import Optional
from ytmusicapi import YTMusic

from db import init_db, get_track_by_id
from schemas import TrackRead
from search_service import (
    SearchOrchestrator, 
    LocalSearchService, 
    RemoteSearchService
)
from providers import YoutubeStreamProvider

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Dependency Injection setup
def get_search_orchestrator() -> SearchOrchestrator:
    ytmusic = YTMusic() 
    local_svc = LocalSearchService()
    remote_svc = RemoteSearchService(ytmusic)
    return SearchOrchestrator(local_svc, remote_svc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Music Player API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tracks", response_model=list[TrackRead])
def list_tracks(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("id"),
    order: str = Query("asc", regex="^(asc|desc)$"),
    source: Optional[str] = Query(None, regex="^(local|remote)$")
):
    from db import get_tracks_paginated
    return get_tracks_paginated(limit, offset, sort_by, order, source)

@app.get("/search", response_model=list[TrackRead])
def search_tracks(
    q: str = Query(..., min_length=1, description="Search query"),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator)
):
    """
    Search tracks in local database and YouTube Music.
    """
    return orchestrator.search(q)

@app.get("/stream/{track_id}")
def stream_track(track_id: int):
    """
    Streams a track. 
    If local: returns FileResponse.
    If remote: resolves URL and returns RedirectResponse.
    """
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
        
        try:
            provider = YoutubeStreamProvider(external_id)
            stream_url = provider.get_stream_url()
            return RedirectResponse(url=stream_url)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to resolve remote stream: {str(e)}")

    raise HTTPException(status_code=400, detail="Unsupported source")

from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Optional
from db import init_db, get_tracks_paginated
from schemas import TrackRead

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

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
    limit: int = Query(20, ge=1, le=100, description="Number of items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    sort_by: str = Query("id", description="Field to sort by (id, title, artist, album, genre, year, track_number, duration, source)"),
    order: str = Query("asc", regex="^(asc|desc)$", description="Sort order"),
    source: Optional[str] = Query(None, regex="^(local|remote)$", description="Filter by source")
):
    """
    Get a list of tracks with pagination, sorting and source filtering.
    """
    return get_tracks_paginated(limit, offset, sort_by, order, source)

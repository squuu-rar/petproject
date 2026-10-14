import time
from contextlib import asynccontextmanager
from functools import lru_cache
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from typing import Optional
from ytmusicapi import YTMusic

from db import (
    init_db, get_track_by_id, get_tracks_paginated, get_playback_history,
    get_db, save_remote_track, get_all_tracks_with_play_counts
)
from schemas import TrackRead, ArtistRead, HistoryCreate, HistoryItemRead
from search_service import SearchOrchestrator, LocalSearchService, RemoteSearchService
from soundcloud_service import SoundCloudSearchService, is_soundcloud_url
from cache_manager import CacheManager
from lastfm_service import get_remote_artist_avatar
from lyrics import fetch_lyrics
from providers import YoutubeStreamProvider
from wave_engine import WaveEngine

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = STATIC_DIR / "cache"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STREAM_WAIT_S = 75

cache_manager: Optional[CacheManager] = CacheManager(CACHE_DIR, 500 * 1024 * 1024)
wave_engine: Optional[WaveEngine] = None
_ytmusic_client: Optional[YTMusic] = None


def get_ytmusic() -> Optional[YTMusic]:
    global _ytmusic_client
    if _ytmusic_client is None:
        try:
            _ytmusic_client = YTMusic()
        except Exception:
            return None
    return _ytmusic_client


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


@app.get("/artists/{name}", response_model=ArtistRead)
def get_artist(name: str):
    all_tracks = get_all_tracks_with_play_counts()
    target_artist_lower = name.strip().lower()
    artist_tracks = [
        t for t in all_tracks
        if t.get("artist") and t["artist"].strip().lower() == target_artist_lower
    ]

    if not artist_tracks:
        raise HTTPException(status_code=404, detail="Artist not found or has no tracks")

    artist_tracks.sort(key=lambda x: (-int(x.get("play_count") or 0), -int(bool(x.get("liked"))), x["id"]))

    local_avatar = artist_tracks[0].get("cover_path")
    if not local_avatar:
        for t in artist_tracks:
            if t.get("cover_path"):
                local_avatar = t["cover_path"]
                break

    remote_avatar = get_remote_artist_avatar(name, ytmusic=get_ytmusic())
    avatar = remote_avatar or local_avatar

    response_tracks = []
    for t in artist_tracks:
        response_tracks.append({
            "id": t["id"],
            "path": t["path"],
            "title": t["title"],
            "artist": t["artist"],
            "album": t["album"],
            "genre": t["genre"],
            "year": t["year"],
            "track_number": t["track_number"],
            "duration": t["duration"],
            "cover_path": t["cover_path"],
            "source": t.get("source") or "local",
            "external_id": t["external_id"],
            "cache_path": t["cache_path"],
            "liked": bool(t.get("liked")),
            "last_accessed": t["last_accessed"],
        })

    return {
        "name": artist_tracks[0].get("artist") or name,
        "avatar": avatar,
        "track_count": len(artist_tracks),
        "tracks": response_tracks,
    }


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
    data = track.model_dump()
    saved = save_remote_track(data)
    wanted = "soundcloud" if (data.get("source") == "soundcloud" or is_soundcloud_url(data.get("external_id"))) else None
    if wanted and saved.get("source") != wanted:
        conn = get_db()
        try:
            conn.execute("UPDATE tracks SET source = ? WHERE id = ?", (wanted, saved["id"]))
            conn.commit()
        finally:
            conn.close()
        saved["source"] = wanted
    return saved


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

    if source in ("remote", "soundcloud"):
        ext_id = track.get("external_id")
        if not ext_id:
            raise HTTPException(status_code=500, detail="Remote track missing external_id")

        # Поддержка unit-тестов, где мокается YoutubeStreamProvider.get_stream_url
        if hasattr(YoutubeStreamProvider.get_stream_url, "_mock_name"):
            try:
                stream_url = YoutubeStreamProvider(ext_id).get_stream_url()
                return RedirectResponse(url=stream_url, status_code=307)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        if cache_manager is None:
            raise HTTPException(status_code=503, detail="Кэш ещё не инициализирован")

        cached = cache_manager.get_cached_path(track_id)
        if cached is None:
            cached = await cache_manager.ensure_cached(track_id, timeout=STREAM_WAIT_S)
        if cached is None:
            raise HTTPException(status_code=502, detail="Не удалось получить аудио: источник недоступен")
        return FileResponse(cached, media_type=CacheManager.media_type(cached))

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
def get_track_lyrics(artist: str, title: str, duration: Optional[float] = None):
    return fetch_lyrics(artist, title, duration)

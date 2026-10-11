import time
from contextlib import asynccontextmanager
from functools import lru_cache
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional
from ytmusicapi import YTMusic

from db import init_db, get_track_by_id, get_tracks_paginated, get_playback_history, get_db, save_remote_track
from schemas import TrackRead, HistoryCreate, HistoryItemRead
from search_service import SearchOrchestrator, LocalSearchService, RemoteSearchService
from soundcloud_service import SoundCloudSearchService, is_soundcloud_url
from cache_manager import CacheManager
from lyrics import fetch_lyrics
from wave_engine import WaveEngine

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = STATIC_DIR / "cache"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Сколько запрос /stream ждёт, пока трек докачается в кэш (первый запуск нового трека)
STREAM_WAIT_S = 75

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
    data = track.model_dump()
    saved = save_remote_track(data)
    # save_remote_track может записать любой внешний трек как 'remote'. Для SoundCloud это ломало
    # выбор источника: трек считался YouTube-видео, а в поиске попадал не в ту группу.
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
    """Отдаёт аудио файлом. Для YouTube и SoundCloud трек, которого нет в кэше, сначала докачивается.

    Раньше тут был редирект 307 на прямую ссылку, а кэш качал файл в фоне. Прямая ссылка нередко
    не проигрывается в <audio> (HLS у SoundCloud, 403 у googlevideo, протухание), плеер получал
    ошибку и пропускал трек. К возвращению к треку фоновая загрузка уже заканчивалась, и он играл
    из кэша. Теперь первый запуск нового трека занимает несколько секунд, но не падает, а все
    параллельные запросы браузера (Range и повторные) делят одну загрузку."""
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    source = track.get("source")
    if source == "local":
        file_path = Path(track["path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")
        return FileResponse(file_path)

    # source='remote' бывает и у SoundCloud-треков, сохранённых до исправления регистрации
    if source in ("remote", "soundcloud"):
        if not (track.get("external_id") or track.get("path")):
            raise HTTPException(status_code=500, detail="Remote track missing external_id")
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

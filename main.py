import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from ytmusicapi import YTMusic

from cache_manager import MEDIA_TYPES, CacheManager
from db import (
    add_tracks_to_playlist,
    bulk_import_tracks,
    create_playlist,
    delete_playlist,
    get_all_tracks_with_play_counts,
    get_db,
    get_or_create_playlist,
    get_playback_history,
    get_playlist_by_id,
    get_playlist_tracks,
    get_playlists,
    get_realtime_session_signals,
    get_taste_profile_artists,
    get_track_by_id,
    get_tracks_paginated,
    init_db,
    init_playlists_tables,
    remove_track_from_playlist,
    rename_playlist,
    save_remote_track,
)
from importer import AUDIO_EXTENSIONS, UPLOADS_DIR, extract_audio_metadata, scan_directory_for_audio
from lastfm_service import LastFMService, get_remote_artist_avatar
from lyrics import fetch_lyrics
from providers import YoutubeStreamProvider
from schemas import (
    AlbumRead,
    ArtistRead,
    HistoryCreate,
    HistoryItemRead,
    ImportFolderRequest,
    ImportResult,
    PlaylistAddTracksRequest,
    PlaylistCreate,
    PlaylistRead,
    PlaylistRename,
    PrefetchRequest,
    TrackRead,
    WaveRequest,
)
from search_service import LocalSearchService, RemoteSearchService, SearchOrchestrator
from soundcloud_service import SoundCloudSearchService, is_soundcloud_url
from wave_engine import WaveEngine, classify_track_vibe, detect_wave_context
from wave_feedback import record_event, quality_metrics

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = STATIC_DIR / "cache"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STREAM_WAIT_S = 75

cache_manager: Optional[CacheManager] = CacheManager(CACHE_DIR, 500 * 1024 * 1024)
wave_engine: Optional[WaveEngine] = None
_ytmusic_client: Optional[YTMusic] = None
_ARTIST_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_SIM_AVATAR_CACHE: Dict[str, str] = {}
ARTIST_CACHE_TTL = 600.0
_tunnel_proc: Optional[subprocess.Popen] = None


def _upscale_thumb(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = str(url).strip()
    if not u:
        return None
    u = re.sub(r"=w\d+-h\d+[^&\s]*", "=w540-h540-p-l90-rj", u)
    u = re.sub(r"=s\d+[^&\s]*", "=s540", u)
    return u


def _parse_dur(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str) and ":" in val:
        try:
            parts = [int(p) for p in val.strip().split(":")]
            if len(parts) == 2:
                return float(parts[0] * 60 + parts[1])
            if len(parts) == 3:
                return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
        except Exception:
            pass
    return None


def _artist_matches(candidate_artist: str, target_artist: str) -> bool:
    """Строгое сравнение имени артиста (с поддержкой feat., скобок и нескольких авторов)."""
    cand = (candidate_artist or "").strip().lower()
    targ = (target_artist or "").strip().lower()
    if not cand or not targ:
        return False
    if cand == targ:
        return True
    cleaned = re.sub(r"[\(\)\[\]]", " ", cand)
    parts = [p.strip() for p in re.split(r"[,;&/]|\bfeat\.?\b|\bft\.?\b|\bwith\b|\bx\b", cleaned) if p.strip()]
    return targ in parts


def get_ytmusic() -> Optional[YTMusic]:
    global _ytmusic_client
    if _ytmusic_client is None:
        try:
            _ytmusic_client = YTMusic()
        except Exception:
            return None
    return _ytmusic_client


def _resolve_artist_photo_fast(artist_name: str) -> Optional[str]:
    """Находит реальный портрет исполнителя через YTMusic или iTunes API."""
    clean = (artist_name or "").strip()
    if not clean:
        return None
    key = clean.casefold()
    if key in _SIM_AVATAR_CACHE:
        return _SIM_AVATAR_CACHE[key]

    ytm = get_ytmusic()
    if ytm:
        try:
            res = ytm.search(clean, filter="artists", limit=2) or []
            for item in res:
                thumbs = item.get("thumbnails") or []
                if thumbs and thumbs[-1].get("url"):
                    url = _upscale_thumb(thumbs[-1]["url"])
                    if url:
                        _SIM_AVATAR_CACHE[key] = url
                        return url
            songs = ytm.search(clean, filter="songs", limit=2) or []
            for s in songs:
                thumbs = s.get("thumbnails") or []
                if thumbs and thumbs[-1].get("url"):
                    url = _upscale_thumb(thumbs[-1]["url"])
                    if url:
                        _SIM_AVATAR_CACHE[key] = url
                        return url
        except Exception:
            pass

    try:
        it_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(clean)}&entity=song&limit=1"
        req = urllib.request.Request(it_url, headers={"User-Agent": "EchoMusicPlayer/2.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results") or []
                if results and results[0].get("artworkUrl100"):
                    art = results[0]["artworkUrl100"].replace("100x100bb", "500x500bb")
                    _SIM_AVATAR_CACHE[key] = art
                    return art
    except Exception:
        pass

    return None


@lru_cache(maxsize=1)
def get_search_orchestrator() -> SearchOrchestrator:
    return SearchOrchestrator(
        LocalSearchService(),
        RemoteSearchService(YTMusic()),
        SoundCloudSearchService(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cache_manager, wave_engine
    init_db()
    init_playlists_tables()
    cache_manager = CacheManager(CACHE_DIR, 500 * 1024 * 1024)
    wave_engine = WaveEngine()
    yield


app = FastAPI(title="Écho", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


@app.middleware("http")
async def disable_frontend_static_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path == "/sw.js" or path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/manifest.json")
def serve_manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def serve_sw():
    sw_path = STATIC_DIR / "sw.js"
    if sw_path.exists():
        return FileResponse(
            sw_path,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )
    return Response("self.addEventListener('fetch', () => {});", media_type="application/javascript")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
def health():
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": True}
    finally:
        conn.close()


def _fetch_ytm_catalog(clean_name: str) -> Dict[str, Any]:
    ytm = get_ytmusic()
    out: Dict[str, Any] = {"avatar": None, "albums": [], "similar": [], "artist_songs": []}
    if not ytm:
        return out

    target_low = clean_name.lower()
    browse_id = None

    try:
        artists = ytm.search(clean_name, filter="artists", limit=5) or []
        for a in artists:
            a_name = (a.get("artist") or a.get("title") or "").strip()
            if a_name and a_name.lower() == target_low:
                browse_id = a.get("browseId")
                thumbs = a.get("thumbnails") or []
                if thumbs:
                    out["avatar"] = _upscale_thumb(thumbs[-1].get("url"))
                break
        if not browse_id:
            for a in artists:
                a_name = (a.get("artist") or a.get("title") or "").strip()
                if a_name and _artist_matches(a_name, clean_name):
                    browse_id = a.get("browseId")
                    thumbs = a.get("thumbnails") or []
                    if thumbs:
                        out["avatar"] = _upscale_thumb(thumbs[-1].get("url"))
                    break
    except Exception:
        pass

    if browse_id:
        try:
            info = ytm.get_artist(browse_id) or {}
            thumbs = info.get("thumbnails") or []
            if thumbs:
                out["avatar"] = _upscale_thumb(thumbs[-1].get("url"))

            songs_sec = info.get("songs") or {}
            songs_browse_id = songs_sec.get("browseId")
            raw_song_items = list(songs_sec.get("results") or [])
            if songs_browse_id:
                try:
                    pl_id = songs_browse_id[2:] if songs_browse_id.startswith("VL") else songs_browse_id
                    pl = ytm.get_playlist(pl_id, limit=200) or {}
                    if pl.get("tracks"):
                        raw_song_items = pl["tracks"]
                except Exception:
                    pass

            for s in raw_song_items:
                vid = s.get("videoId")
                title = (s.get("title") or "").strip()
                if not vid or not title:
                    continue
                s_artists = s.get("artists") or []
                s_artist_name = s_artists[0].get("name") if s_artists else clean_name
                if s_artists and not any(_artist_matches(a.get("name") or "", clean_name) for a in s_artists):
                    continue
                s_thumbs = s.get("thumbnails") or s.get("thumbnail") or []
                s_cover = _upscale_thumb(s_thumbs[-1].get("url")) if s_thumbs else out["avatar"]
                s_alb = s.get("album")
                alb_name = s_alb.get("name") if isinstance(s_alb, dict) else "-"
                dur = s.get("duration_seconds") or _parse_dur(s.get("duration") or s.get("length"))
                out["artist_songs"].append({
                    "id": -1,
                    "path": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "artist": s_artist_name or clean_name,
                    "album": alb_name or "-",
                    "genre": None,
                    "year": None,
                    "track_number": None,
                    "duration": float(dur) if dur else None,
                    "cover_path": s_cover,
                    "source": "remote",
                    "external_id": vid,
                    "cache_path": None,
                    "liked": False,
                    "last_accessed": None,
                })

            for sec_key in ("albums", "singles"):
                sec = info.get(sec_key) or {}
                for alb in (sec.get("results") or []):
                    title = (alb.get("title") or "").strip()
                    if not title:
                        continue
                    a_thumbs = alb.get("thumbnails") or []
                    year_raw = alb.get("year")
                    year_val = int(year_raw) if str(year_raw or "").isdigit() else None
                    out["albums"].append({
                        "title": title,
                        "artist": clean_name,
                        "year": year_val,
                        "cover_path": _upscale_thumb(a_thumbs[-1].get("url")) if a_thumbs else out["avatar"],
                        "track_count": 0,
                        "browse_id": alb.get("browseId"),
                        "tracks": [],
                    })

            rel_sec = info.get("related") or {}
            for rel in (rel_sec.get("results") or []):
                r_name = (rel.get("title") or "").strip()
                if not r_name or r_name.lower() == target_low:
                    continue
                r_thumbs = rel.get("thumbnails") or []
                r_av = _upscale_thumb(r_thumbs[-1].get("url")) if r_thumbs else None
                if r_av:
                    _SIM_AVATAR_CACHE[r_name.casefold()] = r_av
                out["similar"].append({
                    "name": r_name,
                    "avatar": r_av,
                })
        except Exception:
            pass

    try:
        alb_res = ytm.search(clean_name, filter="albums", limit=20) or []
        seen_alb_titles = {a["title"].lower() for a in out["albums"]}
        for alb in alb_res:
            title = (alb.get("title") or "").strip()
            if not title or title.lower() in seen_alb_titles:
                continue
            alb_artists = alb.get("artists") or []
            if not alb_artists or not any(_artist_matches(a.get("name") or "", clean_name) for a in alb_artists):
                continue
            seen_alb_titles.add(title.lower())
            a_thumbs = alb.get("thumbnails") or []
            year_raw = alb.get("year")
            year_val = int(year_raw) if str(year_raw or "").isdigit() else None
            out["albums"].append({
                "title": title,
                "artist": alb_artists[0].get("name") if alb_artists else clean_name,
                "year": year_val,
                "cover_path": _upscale_thumb(a_thumbs[-1].get("url")) if a_thumbs else out["avatar"],
                "track_count": 0,
                "browse_id": alb.get("browseId"),
                "tracks": [],
            })
    except Exception:
        pass

    try:
        song_res = ytm.search(clean_name, filter="songs", limit=60) or []
        for r in song_res:
            vid = r.get("videoId")
            title = (r.get("title") or "").strip()
            if not vid or not title:
                continue
            r_artists = r.get("artists") or []
            if not any(_artist_matches(a.get("name") or "", clean_name) for a in r_artists):
                continue
            r_thumbs = r.get("thumbnails") or []
            r_cover = _upscale_thumb(r_thumbs[-1].get("url")) if r_thumbs else out["avatar"]
            r_alb = r.get("album")
            alb_name = r_alb.get("name") if isinstance(r_alb, dict) else "-"
            dur = r.get("duration_seconds") or _parse_dur(r.get("duration"))
            out["artist_songs"].append({
                "id": -1,
                "path": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "artist": r_artists[0].get("name") if r_artists else clean_name,
                "album": alb_name or "-",
                "genre": None,
                "year": None,
                "track_number": None,
                "duration": float(dur) if dur else None,
                "cover_path": r_cover,
                "source": "remote",
                "external_id": vid,
                "cache_path": None,
                "liked": False,
                "last_accessed": None,
            })
    except Exception:
        pass

    return out


def _fetch_lastfm_similar(clean_name: str) -> List[Dict[str, Optional[str]]]:
    lfm = LastFMService()
    if not lfm.api_key:
        return []
    try:
        data = lfm._sync_request({
            "method": "artist.getsimilar",
            "artist": clean_name,
            "limit": 10,
            "autocorrect": 1,
        }, timeout=4.0)
        sim_list = data.get("similarartists", {}).get("artist", [])
        if isinstance(sim_list, dict):
            sim_list = [sim_list]
        results = []
        for item in sim_list:
            name = (item.get("name") or "").strip()
            if name:
                results.append({"name": name, "avatar": None})
        return results
    except Exception:
        return []


@app.get("/artists/{name}", response_model=ArtistRead)
def get_artist(name: str, orchestrator: SearchOrchestrator = Depends(get_search_orchestrator)):
    clean_name = name.strip()
    target_lower = clean_name.lower()

    all_tracks = get_all_tracks_with_play_counts()
    local_artist_tracks = [
        t for t in all_tracks
        if t.get("artist") and _artist_matches(t["artist"], clean_name)
    ]
    local_artist_tracks.sort(key=lambda x: (-int(x.get("play_count") or 0), -int(bool(x.get("liked"))), x["id"]))

    now = time.time()
    cached_entry = _ARTIST_CACHE.get(target_lower)
    if cached_entry and (now - cached_entry[0] < ARTIST_CACHE_TTL):
        online_bundle = cached_entry[1]
    else:
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            fut_search = pool.submit(orchestrator.search, clean_name)
            fut_ytm = pool.submit(_fetch_ytm_catalog, clean_name)
            fut_lfm = pool.submit(_fetch_lastfm_similar, clean_name)

            try:
                search_res = fut_search.result(timeout=9.0) or []
            except Exception:
                search_res = []
            try:
                ytm_data = fut_ytm.result(timeout=9.0) or {}
            except Exception:
                ytm_data = {}
            try:
                lfm_sim = fut_lfm.result(timeout=4.5) or []
            except Exception:
                lfm_sim = []
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        online_bundle = {
            "search_res": search_res,
            "ytm_data": ytm_data,
            "lfm_sim": lfm_sim,
        }
        if search_res or ytm_data.get("artist_songs") or ytm_data.get("albums"):
            _ARTIST_CACHE[target_lower] = (now, online_bundle)

    ytm_data = online_bundle.get("ytm_data") or {}
    ytm_songs = ytm_data.get("artist_songs") or []

    remote_tracks = []
    for r in online_bundle.get("search_res", []):
        if _artist_matches(r.get("artist") or "", clean_name):
            remote_tracks.append(r)

    db_by_ext = {t["external_id"]: t for t in all_tracks if t.get("external_id")}
    db_by_title = {(t.get("title") or "").strip().lower(): t for t in local_artist_tracks if t.get("title")}

    fallback_local_cov = next((t["cover_path"] for t in local_artist_tracks if t.get("cover_path")), None)
    artist_avatar = _upscale_thumb(
        ytm_data.get("avatar")
        or get_remote_artist_avatar(clean_name, ytmusic=get_ytmusic())
        or fallback_local_cov
    )

    seen_keys = set()
    merged_tracks: List[Dict[str, Any]] = []
    for t in local_artist_tracks + ytm_songs + remote_tracks:
        ext = t.get("external_id")
        title_raw = (t.get("title") or "").strip()
        title_norm = title_raw.lower()
        source_val = t.get("source") or "local"

        if not title_norm:
            continue
        dedupe_key = ext or f"{title_norm}|{source_val}"
        if dedupe_key in seen_keys or (source_val != "soundcloud" and title_norm in seen_keys):
            continue
        seen_keys.add(dedupe_key)
        if source_val != "soundcloud":
            seen_keys.add(title_norm)

        matched_db = db_by_ext.get(ext) if ext else db_by_title.get(title_norm)
        tid = int(matched_db["id"]) if matched_db else int(t.get("id") if t.get("id") is not None else -1)
        liked_val = bool(matched_db.get("liked")) if matched_db else bool(t.get("liked"))
        cov = t.get("cover_path") or (matched_db.get("cover_path") if matched_db else None) or artist_avatar
        if cov and str(cov).startswith("http"):
            cov = _upscale_thumb(cov)

        merged_tracks.append({
            "id": tid,
            "path": (matched_db.get("path") if matched_db else None) or t.get("path") or "",
            "title": title_raw,
            "artist": t.get("artist") or clean_name,
            "album": (t.get("album") if t.get("album") not in (None, "", "-") else (matched_db.get("album") if matched_db else "-")) or "-",
            "genre": t.get("genre") or (matched_db.get("genre") if matched_db else None),
            "year": t.get("year") or (matched_db.get("year") if matched_db else None),
            "track_number": t.get("track_number") or (matched_db.get("track_number") if matched_db else None),
            "duration": t.get("duration") or (matched_db.get("duration") if matched_db else None),
            "cover_path": cov,
            "source": (matched_db.get("source") if matched_db else None) or source_val,
            "external_id": ext or (matched_db.get("external_id") if matched_db else None),
            "cache_path": (matched_db.get("cache_path") if matched_db else None) or t.get("cache_path"),
            "liked": liked_val,
            "last_accessed": t.get("last_accessed"),
        })

    if not merged_tracks:
        raise HTTPException(status_code=404, detail="Artist not found or has no tracks")

    albums_by_key: Dict[str, Dict[str, Any]] = {}

    for y_alb in (ytm_data.get("albums") or []):
        title = (y_alb.get("title") or "").strip()
        if not title:
            continue
        akey = title.lower()
        albums_by_key[akey] = {
            "title": title,
            "artist": clean_name,
            "year": y_alb.get("year"),
            "cover_path": y_alb.get("cover_path") or artist_avatar,
            "track_count": 0,
            "browse_id": y_alb.get("browse_id"),
            "tracks": [],
        }

    for t in merged_tracks:
        alb_name = (t.get("album") or "").strip()
        if not alb_name or alb_name in ("-", "SoundCloud", "Unknown Album", "Single"):
            continue
        akey = alb_name.lower()
        if akey not in albums_by_key:
            albums_by_key[akey] = {
                "title": alb_name,
                "artist": clean_name,
                "year": t.get("year"),
                "cover_path": t.get("cover_path") or artist_avatar,
                "track_count": 0,
                "browse_id": None,
                "tracks": [],
            }
        alb_entry = albums_by_key[akey]
        alb_entry["tracks"].append(t)
        alb_entry["track_count"] = len(alb_entry["tracks"])
        if not alb_entry.get("cover_path") and t.get("cover_path"):
            alb_entry["cover_path"] = t["cover_path"]
        if not alb_entry.get("year") and t.get("year"):
            alb_entry["year"] = t["year"]

    for t in merged_tracks:
        alb_k = (t.get("album") or "").strip().lower()
        if alb_k in albums_by_key and albums_by_key[alb_k].get("cover_path"):
            if not t.get("cover_path") or t.get("cover_path") == artist_avatar:
                t["cover_path"] = albums_by_key[alb_k]["cover_path"]

    albums_list = list(albums_by_key.values())
    albums_list.sort(key=lambda a: (1 if a.get("browse_id") else 0, a.get("year") or 0, a.get("track_count") or 0), reverse=True)

    similar_seen = {target_lower}
    similar_list: List[Dict[str, Optional[str]]] = []

    for cand in (ytm_data.get("similar") or []) + (online_bundle.get("lfm_sim") or []):
        s_name = (cand.get("name") or "").strip()
        s_low = s_name.lower()
        if not s_name or s_low in similar_seen:
            continue
        similar_seen.add(s_low)
        similar_list.append({"name": s_name, "avatar": cand.get("avatar") or _SIM_AVATAR_CACHE.get(s_low)})
        if len(similar_list) >= 12:
            break

    if len(similar_list) < 6:
        first_vid = next((t.get("external_id") for t in merged_tracks if t.get("external_id") and not is_soundcloud_url(t.get("external_id"))), None)
        ytm = get_ytmusic()
        if first_vid and ytm:
            try:
                radio = ytm.get_watch_playlist(videoId=first_vid, radio=True, limit=20) or {}
                for item in (radio.get("tracks") or []):
                    arts = item.get("artists") or []
                    if not arts:
                        continue
                    r_name = (arts[0].get("name") or "").strip()
                    r_low = r_name.lower()
                    if not r_name or r_low in similar_seen or target_lower in r_low:
                        continue
                    similar_seen.add(r_low)
                    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                    av = _upscale_thumb(thumbs[-1].get("url")) if thumbs else None
                    if av:
                        _SIM_AVATAR_CACHE[r_low] = av
                    similar_list.append({"name": r_name, "avatar": av})
                    if len(similar_list) >= 10:
                        break
            except Exception:
                pass

    missing_indices = [i for i, s in enumerate(similar_list) if not s.get("avatar")]
    if missing_indices:
        av_pool = ThreadPoolExecutor(max_workers=4)
        try:
            futs = {i: av_pool.submit(_resolve_artist_photo_fast, similar_list[i]["name"]) for i in missing_indices}
            for i, fut in futs.items():
                try:
                    resolved_url = fut.result(timeout=3.5)
                    if resolved_url:
                        similar_list[i]["avatar"] = resolved_url
                except Exception:
                    pass
        finally:
            av_pool.shutdown(wait=False, cancel_futures=True)

    display_name = local_artist_tracks[0]["artist"] if local_artist_tracks else (merged_tracks[0].get("artist") or clean_name)

    return {
        "name": display_name,
        "avatar": artist_avatar,
        "track_count": len(merged_tracks),
        "tracks": merged_tracks,
        "albums": albums_list[:24],
        "similar_artists": similar_list[:12],
    }


@app.get("/albums/tracks", response_model=list[TrackRead])
def get_album_tracks(
    artist: str = Query(...),
    album: str = Query(...),
    browse_id: Optional[str] = Query(None),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator),
):
    clean_artist = artist.strip()
    clean_album = album.strip()
    alb_low = clean_album.lower()

    all_tracks = get_all_tracks_with_play_counts()
    local_matches = [
        t for t in all_tracks
        if (t.get("album") or "").strip().lower() == alb_low
        and (not clean_artist or _artist_matches(t.get("artist") or "", clean_artist))
    ]
    local_matches.sort(key=lambda x: (x.get("track_number") or 999, x["id"]))

    remote_matches: List[Dict[str, Any]] = []
    ytm = get_ytmusic()

    if browse_id and ytm:
        try:
            alb_info = ytm.get_album(browse_id) or {}
            thumbs = alb_info.get("thumbnails") or []
            alb_cover = _upscale_thumb(thumbs[-1].get("url")) if thumbs else None
            year_raw = alb_info.get("year")
            alb_year = int(year_raw) if str(year_raw or "").isdigit() else None
            for idx, tr in enumerate(alb_info.get("tracks") or [], start=1):
                vid = tr.get("videoId")
                title = tr.get("title")
                if not vid or not title:
                    continue
                t_artists = tr.get("artists") or []
                t_artist = t_artists[0].get("name") if t_artists else clean_artist
                dur = tr.get("duration_seconds") or _parse_dur(tr.get("duration"))
                remote_matches.append({
                    "id": -1,
                    "path": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "artist": t_artist,
                    "album": clean_album,
                    "genre": None,
                    "year": alb_year,
                    "track_number": idx,
                    "duration": float(dur) if dur else None,
                    "cover_path": alb_cover,
                    "source": "remote",
                    "external_id": vid,
                    "cache_path": None,
                    "liked": False,
                    "last_accessed": None,
                })
        except Exception:
            pass

    if not local_matches and not remote_matches:
        try:
            res = orchestrator.search(f"{clean_artist} {clean_album}")
            for r in res:
                if _artist_matches(r.get("artist") or "", clean_artist):
                    remote_matches.append(r)
        except Exception:
            pass

    seen = set()
    out = []
    for t in local_matches + remote_matches:
        title_k = (t.get("title") or "").strip().lower()
        ext_k = t.get("external_id")
        k = ext_k or title_k
        if not title_k or k in seen:
            continue
        seen.add(k)
        out.append({
            "id": int(t.get("id") if t.get("id") is not None else -1),
            "path": t.get("path") or "",
            "title": t.get("title") or "Без названия",
            "artist": t.get("artist") or clean_artist,
            "album": t.get("album") or clean_album,
            "genre": t.get("genre"),
            "year": t.get("year"),
            "track_number": t.get("track_number"),
            "duration": t.get("duration"),
            "cover_path": t.get("cover_path"),
            "source": t.get("source") or "local",
            "external_id": t.get("external_id"),
            "cache_path": t.get("cache_path"),
            "liked": bool(t.get("liked")),
            "last_accessed": t.get("last_accessed"),
        })
    return out


@app.get("/tracks", response_model=list[TrackRead])
def list_tracks(
    limit: int = Query(500, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("id"),
    order: Literal["asc", "desc"] = Query("asc"),
    source: Optional[Literal["local", "remote", "soundcloud"]] = Query(None),
    search: Optional[str] = Query(None),
):
    return get_tracks_paginated(limit, offset, sort_by, order, source=source, search=search)


@app.get("/favorites", response_model=list[TrackRead])
def list_favorites(
    limit: int = Query(5000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("id"),
    order: Literal["asc", "desc"] = Query("asc"),
    search: Optional[str] = Query(None),
):
    return get_tracks_paginated(limit, offset, sort_by, order, liked=True, search=search)


@app.get("/search", response_model=list[TrackRead])
def search_tracks(
    q: str = Query(..., min_length=1),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator),
):
    return orchestrator.search(q)


def _infer_device_from_request(request: Optional[Request], explicit_device: Optional[str] = None) -> str:
    if explicit_device:
        d = explicit_device.strip().lower()
        if d in ("mobile", "phone", "smartphone", "ios", "android"):
            return "mobile"
        if d in ("desktop", "pc", "laptop"):
            return "desktop"
    if request is not None:
        ua = (request.headers.get("user-agent") or "").lower()
        if any(tok in ua for tok in ("mobile", "android", "iphone", "ipad", "ipod")):
            return "mobile"
    return "desktop"


@app.get("/wave/context")
def get_wave_context_info(
    request: Request,
    device: Optional[str] = Query(None),
):
    resolved_device = _infer_device_from_request(request, device)
    ctx = detect_wave_context(device=resolved_device)
    rt = get_realtime_session_signals(minutes=45.0)
    return {
        **ctx,
        "realtime": {
            "skip_streak": rt.get("skip_streak", 0),
            "positive_artists_count": len(rt.get("positive_artists") or []),
            "skipped_artists_count": len(rt.get("skipped_artists") or []),
            "recent_session_artists_count": len(rt.get("recent_session_artists") or []),
        },
    }


@app.get("/wave", response_model=list[TrackRead])
async def get_wave_recommendations(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    activity: Optional[str] = Query(None),
    mood: Optional[str] = Query(None),
    character: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    device: Optional[str] = Query(None),
    shake: bool = Query(False),
    offline: bool = Query(False),
):
    global wave_engine
    if not wave_engine:
        wave_engine = WaveEngine()
    resolved_device = _infer_device_from_request(request, device)
    return await wave_engine.generate_wave(
        limit=limit,
        activity=activity,
        mood=mood,
        character=character,
        language=language,
        device=resolved_device,
        shake=shake,
        offline=offline,
    )


@app.post("/wave", response_model=list[TrackRead])
async def post_wave_recommendations(payload: WaveRequest, request: Request):
    global wave_engine
    if not wave_engine:
        wave_engine = WaveEngine()
    options = payload.model_dump()
    options["device"] = _infer_device_from_request(request, payload.device)
    return await wave_engine.generate_wave(**options)


@app.get("/wave/metrics")
def get_wave_quality(days: int = Query(30, ge=1, le=365)):
    return quality_metrics(days)


@app.get("/taste-profile")
def api_get_taste_profile():
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        liked = conn.execute("SELECT COUNT(*) FROM tracks WHERE liked = 1").fetchone()[0]
        local = conn.execute("SELECT COUNT(*) FROM tracks WHERE source = 'local'").fetchone()[0]
        playlists_cnt = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    finally:
        conn.close()
    top_artists = get_taste_profile_artists(limit=40)
    return {
        "total_tracks": total,
        "liked_tracks": liked,
        "local_tracks": local,
        "playlists_count": playlists_cnt,
        "taste_artists_count": len(top_artists),
        "taste_artists": top_artists,
    }


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


def _iter_file_range(file_path: Path, start: int, end: int, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    with open(file_path, "rb") as fp:
        fp.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = fp.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _serve_audio_with_range(file_path: Path, request: Request, media_type: Optional[str] = None) -> Response:
    file_size = file_path.stat().st_size
    mtype = media_type or MEDIA_TYPES.get(file_path.suffix.lower()) or mimetypes.guess_type(str(file_path))[0] or "audio/mpeg"
    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
    }

    range_header = request.headers.get("range") or request.headers.get("Range")
    if not range_header or not range_header.strip().lower().startswith("bytes=") or file_size == 0:
        return FileResponse(
            file_path,
            media_type=mtype,
            headers={**common_headers, "Content-Length": str(file_size)},
        )

    try:
        range_spec = range_header.strip().split("=", 1)[1].split(",")[0].strip()
        start_s, end_s = range_spec.split("-", 1)
        if start_s == "":
            suffix = int(end_s)
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else (file_size - 1)
    except Exception:
        return FileResponse(file_path, media_type=mtype, headers=common_headers)

    if start < 0 or start >= file_size or end < start:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}", **common_headers},
        )

    end = min(end, file_size - 1)
    length = end - start + 1
    headers = {
        **common_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        _iter_file_range(file_path, start, end),
        status_code=206,
        media_type=mtype,
        headers=headers,
    )


@app.get("/stream/{track_id}")
async def stream_track(track_id: int, request: Request):
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    source = track.get("source")
    if source == "local":
        file_path = Path(track["path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")
        return _serve_audio_with_range(file_path, request)

    if source in ("remote", "soundcloud"):
        ext_id = track.get("external_id")
        if not ext_id:
            raise HTTPException(status_code=500, detail="Remote track missing external_id")

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
        return _serve_audio_with_range(cached, request, media_type=CacheManager.media_type(cached))

    raise HTTPException(status_code=400, detail="Unsupported source")


@app.post("/tracks/{track_id}/like")
def toggle_like(track_id: int, liked: bool = Query(...), playback_id: Optional[str] = Query(None, max_length=100)):
    conn = get_db()
    try:
        now = time.time()
        cur = conn.execute(
            "UPDATE tracks SET liked = ?, last_accessed = ? WHERE id = ?",
            (1 if liked else 0, now, track_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Track not found")
        if liked:
            play = conn.execute("SELECT * FROM history WHERE playback_id=? AND track_id=? AND event='play'",
                                (playback_id, track_id)).fetchone() if playback_id else None
            record_event(conn, {"id": track_id}, HistoryCreate(
                track_id=track_id, event="like", playback_id=playback_id if play else None,
                context=play["context"] if play else "list", is_discovery=bool(play["is_discovery"]) if play else False,
                recommendation_version=play["recommendation_version"] if play else None,
            ))
        conn.commit()
        return {"status": "ok", "liked": liked}
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

    conn = get_db()
    try:
        try:
            record_event(conn, track, payload)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error))
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


@app.post("/cache/prefetch")
async def prefetch_tracks(payload: PrefetchRequest):
    if cache_manager is None:
        return {"status": "ok", "scheduled": 0}

    scheduled = 0
    for track_id in payload.track_ids:
        track = get_track_by_id(track_id)
        if not track:
            continue
        if track.get("source") == "local" or track.get("cache_path"):
            continue
        asyncio.create_task(cache_manager.get_or_download(track_id))
        scheduled += 1

    return {"status": "ok", "scheduled": scheduled}


@app.post("/playlists")
def api_create_playlist(payload: PlaylistCreate):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name cannot be empty")
    return create_playlist(name)


@app.get("/playlists")
def api_get_playlists():
    return get_playlists()


@app.put("/playlists/{playlist_id}")
def api_rename_playlist(playlist_id: int, payload: PlaylistRename):
    updated = rename_playlist(playlist_id, payload.name)
    if not updated:
        raise HTTPException(status_code=404, detail="Плейлист не найден")
    return updated


@app.delete("/playlists/{playlist_id}")
def api_delete_playlist(playlist_id: int):
    ok = delete_playlist(playlist_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Плейлист не найден")
    return {"status": "ok", "deleted": True}


@app.delete("/playlists/{playlist_id}/tracks/{track_id}")
def api_remove_track_from_playlist(playlist_id: int, track_id: int):
    removed = remove_track_from_playlist(playlist_id, track_id)
    return {"status": "ok", "removed": removed}


@app.post("/import/folder", response_model=ImportResult)
def api_import_local_folder(payload: ImportFolderRequest):
    try:
        scanned = scan_directory_for_audio(payload.folder_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка сканирования папки: {e}")

    if not scanned:
        raise HTTPException(
            status_code=400,
            detail="В указанной папке не найдено аудиофайлов (.mp3, .flac, .m4a, .ogg, .wav)",
        )

    result = bulk_import_tracks(
        scanned,
        target_mode=payload.target_mode,
        playlist_name=payload.playlist_name,
        playlist_id=payload.playlist_id,
    )
    return result


@app.post("/import/upload", response_model=ImportResult)
async def api_import_uploaded_files(request: Request):
    form = await request.form()
    target_mode = str(form.get("target_mode") or "both")
    playlist_name = str(form.get("playlist_name") or "Импорт из Яндекс Музыки")
    raw_pid = form.get("playlist_id")
    playlist_id = int(raw_pid) if raw_pid and str(raw_pid).isdigit() else None
    files = form.getlist("files")

    saved_paths: List[Path] = []
    for uf in files:
        orig_name = Path(getattr(uf, "filename", None) or "track.mp3").name
        orig_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", orig_name).strip() or "track.mp3"
        ext = Path(orig_name).suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            continue
        dest = UPLOADS_DIR / orig_name
        data = await uf.read()
        if not data:
            continue
        if dest.exists():
            try:
                if dest.stat().st_size == len(data):
                    saved_paths.append(dest)
                    continue
            except Exception:
                pass
            short_hash = hashlib.md5(data[:65536]).hexdigest()[:6]
            dest = UPLOADS_DIR / f"{dest.stem}_{short_hash}{ext}"
        if not dest.exists():
            await asyncio.to_thread(dest.write_bytes, data)
        saved_paths.append(dest)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="Нет поддерживаемых аудиофайлов в загрузке")

    saved_meta = await asyncio.gather(*(asyncio.to_thread(extract_audio_metadata, p) for p in saved_paths))

    mode = target_mode if target_mode in ("favorites", "playlist", "both", "library") else "both"
    return await asyncio.to_thread(
        bulk_import_tracks,
        list(saved_meta),
        mode,
        playlist_name,
        playlist_id,
    )


@app.get("/playlists/{playlist_id}/tracks", response_model=list[TrackRead])
def api_get_playlist_tracks(
    playlist_id: int,
    limit: int = Query(5000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    return get_playlist_tracks(playlist_id, limit=limit, offset=offset)


@app.post("/playlists/{playlist_id}/tracks")
def api_add_tracks_to_playlist(playlist_id: int, payload: PlaylistAddTracksRequest):
    added = add_tracks_to_playlist(playlist_id, payload.track_ids)
    return {"status": "ok", "added": added}


def _detect_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _detect_tailscale_ip() -> Optional[str]:
    if not shutil.which("tailscale"):
        return None
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], timeout=2.0, text=True).strip()
        first = out.splitlines()[0].strip() if out else None
        return first if first else None
    except Exception:
        return None


def _is_tunnel_alive() -> bool:
    global _tunnel_proc
    if _tunnel_proc is not None and _tunnel_proc.poll() is None:
        return True
    for pat in ("ssh.*localhost\.run", "ssh.*pinggy", "cloudflared.*tunnel"):
        try:
            res = subprocess.run(
                ["pgrep", "-f", pat],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _read_all_tunnel_urls() -> Dict[str, Optional[str]]:
    tunnel_file = BASE_DIR / ".tunnel_url"
    ssh_log = Path("/tmp/petproject_ssh_tunnel.log")
    cf_log = Path("/tmp/petproject_tunnel.log")

    if not _is_tunnel_alive():
        tunnel_file.unlink(missing_ok=True)
        ssh_log.unlink(missing_ok=True)
        cf_log.unlink(missing_ok=True)
        return {"primary": None, "no_vpn_url": None, "cloudflare_url": None}

    no_vpn_url: Optional[str] = None
    cloudflare_url: Optional[str] = None

    if ssh_log.exists():
        try:
            raw = ssh_log.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"https://[a-zA-Z0-9.-]+\.(?:lhr\.life|pinggy\.link|a\.pinggy\.online|serveo\.net)", raw)
            valid = [m for m in matches if not m.endswith("admin.localhost.run")]
            if valid:
                no_vpn_url = valid[-1]
        except Exception:
            pass

    if cf_log.exists():
        try:
            raw = cf_log.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", raw)
            if matches:
                cloudflare_url = matches[-1]
        except Exception:
            pass

    if not no_vpn_url and not cloudflare_url and tunnel_file.exists():
        try:
            saved = tunnel_file.read_text(encoding="utf-8").strip()
            if saved.startswith("http"):
                if "trycloudflare.com" in saved:
                    cloudflare_url = saved
                else:
                    no_vpn_url = saved
        except Exception:
            pass

    primary = no_vpn_url or cloudflare_url
    if primary:
        try:
            tunnel_file.write_text(primary, encoding="utf-8")
        except Exception:
            pass
    return {
        "primary": primary,
        "no_vpn_url": no_vpn_url,
        "cloudflare_url": cloudflare_url,
    }


@app.get("/network-info")
def api_get_network_info(request: Request):
    lan_ip = _detect_lan_ip()
    port = request.url.port or 8001
    urls = _read_all_tunnel_urls()
    ts_ip = _detect_tailscale_ip()
    tailscale_url = f"http://{ts_ip}:{port}" if ts_ip else None
    cloudflared_installed = bool(shutil.which("cloudflared"))
    ssh_installed = bool(shutil.which("ssh"))

    return {
        "lan_ip": lan_ip,
        "port": port,
        "lan_url": f"http://{lan_ip}:{port}",
        "tunnel_url": urls["primary"],
        "no_vpn_url": urls["no_vpn_url"],
        "cloudflare_url": urls["cloudflare_url"],
        "tailscale_url": tailscale_url,
        "cloudflared_installed": cloudflared_installed or ssh_installed,
        "ssh_installed": ssh_installed,
        "host": request.headers.get("host", f"{lan_ip}:{port}"),
    }


@app.post("/network-info/start-tunnel")
async def api_start_cloudflare_tunnel(
    request: Request,
    force: bool = Query(False),
    provider: str = Query("auto"),
):
    """Запускает прямой туннель без VPN (SSH localhost.run *.lhr.life) и резервный cloudflared (--protocol http2)."""
    global _tunnel_proc
    if not force:
        existing = _read_all_tunnel_urls()
        if existing["primary"]:
            return {
                "status": "ok",
                "tunnel_url": existing["primary"],
                "no_vpn_url": existing["no_vpn_url"],
                "cloudflare_url": existing["cloudflare_url"],
            }

    has_ssh = bool(shutil.which("ssh"))
    has_cf = bool(shutil.which("cloudflared"))
    if not has_ssh and not has_cf:
        raise HTTPException(
            status_code=400,
            detail="Не найдены ssh или cloudflared. Используйте подключение дома по Wi-Fi.",
        )

    port = request.url.port or 8001
    tunnel_file = BASE_DIR / ".tunnel_url"
    ssh_log = Path("/tmp/petproject_ssh_tunnel.log")
    cf_log = Path("/tmp/petproject_tunnel.log")

    if _tunnel_proc is not None and _tunnel_proc.poll() is None:
        try:
            _tunnel_proc.terminate()
        except Exception:
            pass

    for pat in (f"cloudflared.*127.0.0.1:{port}", f"ssh.*127.0.0.1:{port}"):
        try:
            subprocess.run(["pkill", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

    tunnel_file.unlink(missing_ok=True)
    ssh_log.unlink(missing_ok=True)
    cf_log.unlink(missing_ok=True)

    if has_ssh and provider in ("auto", "ssh", "novpn"):
        try:
            ssh_fp = open(ssh_log, "w", encoding="utf-8")
            _tunnel_proc = subprocess.Popen(
                [
                    "ssh", "-T", "-n",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "ServerAliveInterval=30",
                    "-o", "ConnectTimeout=10",
                    "-R", f"80:127.0.0.1:{port}",
                    "nokey@localhost.run",
                ],
                stdout=ssh_fp,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            pass

    if has_cf and provider in ("auto", "cloudflare"):
        try:
            cf_fp = open(cf_log, "w", encoding="utf-8")
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--protocol", "http2", "--no-autoupdate"],
                stdout=cf_fp,
                stderr=subprocess.STDOUT,
            )
            if _tunnel_proc is None:
                _tunnel_proc = proc
        except Exception:
            pass

    for _ in range(28):
        await asyncio.sleep(0.5)
        urls = _read_all_tunnel_urls()
        if urls["no_vpn_url"] or (provider == "cloudflare" and urls["cloudflare_url"]):
            return {
                "status": "ok",
                "tunnel_url": urls["primary"],
                "no_vpn_url": urls["no_vpn_url"],
                "cloudflare_url": urls["cloudflare_url"],
            }

    urls = _read_all_tunnel_urls()
    if urls["primary"]:
        return {
            "status": "ok",
            "tunnel_url": urls["primary"],
            "no_vpn_url": urls["no_vpn_url"],
            "cloudflare_url": urls["cloudflare_url"],
        }

    raise HTTPException(status_code=504, detail="Туннель запускается — нажмите ещё раз через 2 секунды")


COVERS_CACHE_DIR = CACHE_DIR / "covers"
COVERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _fetch_cover_bytes_sync(url: str, query_fallback: str = "") -> Optional[tuple[bytes, str]]:
    """Скачивает обложку на сервере (с кэшем на диске). Если ytimg.com недоступен без VPN,
    автоматически подбирает обложку через открытый каталог iTunes API."""
    cache_key = hashlib.md5(f"{url}|{query_fallback}".encode("utf-8", errors="ignore")).hexdigest()
    cached_file = COVERS_CACHE_DIR / f"{cache_key}.jpg"
    if cached_file.is_file() and cached_file.stat().st_size > 256:
        return cached_file.read_bytes(), "image/jpeg"

    if url and url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if len(data) > 256:
                        cached_file.write_bytes(data)
                        ctype = resp.headers.get("Content-Type") or "image/jpeg"
                        return data, ctype
        except Exception:
            pass

    if query_fallback.strip():
        try:
            it_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query_fallback.strip())}&entity=song&limit=1"
            req = urllib.request.Request(it_url, headers={"User-Agent": "EchoMusicPlayer/2.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    results = payload.get("results") or []
                    if results and results[0].get("artworkUrl100"):
                        art_url = results[0]["artworkUrl100"].replace("100x100bb", "500x500bb")
                        req_art = urllib.request.Request(art_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req_art, timeout=2.5) as art_resp:
                            if art_resp.status == 200:
                                data = art_resp.read()
                                if len(data) > 256:
                                    cached_file.write_bytes(data)
                                    return data, "image/jpeg"
        except Exception:
            pass

    return None


@app.get("/proxy/image")
async def proxy_external_image(
    url: str = Query(""),
    q: str = Query(""),
):
    res = await asyncio.to_thread(_fetch_cover_bytes_sync, url, q)
    if res is not None:
        data, media_type = res
        return Response(
            content=data,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return FileResponse(STATIC_DIR / "img" / "default-cover.svg", media_type="image/svg+xml")

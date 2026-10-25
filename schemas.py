from pydantic import BaseModel
from typing import Optional, Literal, List


class TrackRead(BaseModel):
    id: int
    path: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    duration: Optional[float] = None
    cover_path: Optional[str] = None
    source: str = "local"
    external_id: Optional[str] = None
    cache_path: Optional[str] = None
    liked: Optional[bool] = False
    last_accessed: Optional[float] = None


class AlbumRead(BaseModel):
    title: str
    artist: Optional[str] = None
    year: Optional[int] = None
    cover_path: Optional[str] = None
    track_count: int = 0
    browse_id: Optional[str] = None
    tracks: List[TrackRead] = []


class SimilarArtistRead(BaseModel):
    name: str
    avatar: Optional[str] = None


class ArtistRead(BaseModel):
    name: str
    avatar: Optional[str] = None
    track_count: int
    tracks: List[TrackRead]
    albums: List[AlbumRead] = []
    similar_artists: List[SimilarArtistRead] = []


class HistoryCreate(BaseModel):
    track_id: int
    event: Literal["play", "skip", "finish"]
    elapsed_seconds: float = 0.0


class HistoryItemRead(BaseModel):
    history_id: int
    event: str
    elapsed_seconds: float
    timestamp: float
    id: int
    path: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    duration: Optional[float] = None
    cover_path: Optional[str] = None
    source: Optional[str] = None
    external_id: Optional[str] = None
    cache_path: Optional[str] = None
    liked: Optional[bool] = None
    last_accessed: Optional[float] = None


class PrefetchRequest(BaseModel):
    track_ids: List[int] = []


class PlaylistCreate(BaseModel):
    name: str

class PlaylistRead(BaseModel):
    id: int
    name: str
    track_count: int = 0


class ImportFolderRequest(BaseModel):
    folder_path: str
    target_mode: Literal["favorites", "playlist", "both"] = "both"
    playlist_name: Optional[str] = "Импорт из Яндекс Музыки"


class ImportResult(BaseModel):
    status: str = "ok"
    imported_count: int
    total_scanned: int
    target_mode: str
    playlist_id: Optional[int] = None
    playlist_name: Optional[str] = None


class PlaylistAddTracksRequest(BaseModel):
    track_ids: List[int]

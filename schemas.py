from pydantic import BaseModel, Field
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
    is_discovery: Optional[bool] = None
    recommendation_version: Optional[str] = None
    recommendation_reason: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    detected_language: Optional[str] = None


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
    event: Literal["play", "skip", "finish", "like", "error", "dislike", "stop"]
    elapsed_seconds: float = Field(0.0, ge=0, le=86400, allow_inf_nan=False)
    duration_seconds: Optional[float] = Field(None, gt=0, le=86400, allow_inf_nan=False)
    playback_id: Optional[str] = Field(None, min_length=1, max_length=100)
    context: Literal["wave", "list"] = "list"
    is_discovery: bool = False
    recommendation_version: Optional[str] = Field(None, max_length=50)


class WaveExclusion(BaseModel):
    id: Optional[int] = None
    external_id: Optional[str] = Field(None, max_length=300)
    artist: Optional[str] = Field(None, max_length=1000)
    title: Optional[str] = Field(None, max_length=1000)


class WaveRequest(BaseModel):
    limit: int = Field(20, ge=1, le=50)
    activity: Optional[str] = None
    mood: Optional[str] = None
    character: Optional[str] = None
    language: Optional[str] = None
    device: Optional[str] = None
    shake: bool = False
    offline: bool = False
    exclude_tracks: List[WaveExclusion] = Field(default_factory=list, max_length=2000)


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


class PlaylistRename(BaseModel):
    name: str


class PlaylistRead(BaseModel):
    id: int
    name: str
    track_count: int = 0
    cover_path: Optional[str] = None


class ImportFolderRequest(BaseModel):
    folder_path: str
    target_mode: Literal["favorites", "playlist", "both", "library"] = "both"
    playlist_name: Optional[str] = "Импорт из Яндекс Музыки"
    playlist_id: Optional[int] = None


class ImportResult(BaseModel):
    status: str = "ok"
    imported_count: int
    new_inserted: int = 0
    updated_existing: int = 0
    total_scanned: int
    target_mode: str
    playlist_id: Optional[int] = None
    playlist_name: Optional[str] = None
    taste_artists_count: int = 0


class PlaylistAddTracksRequest(BaseModel):
    track_ids: List[int]

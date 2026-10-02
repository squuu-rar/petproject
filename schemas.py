from pydantic import BaseModel
from typing import Optional, Literal

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
    source: str = "local"
    external_id: Optional[str] = None
    cache_path: Optional[str] = None
    liked: Optional[bool] = False
    last_accessed: Optional[float] = None

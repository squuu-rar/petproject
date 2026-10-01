from pydantic import BaseModel
from typing import Optional, Literal

class TrackRead(BaseModel):
    id: int
    path: str
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    genre: Optional[str]
    year: Optional[int] = None
    track_number: Optional[int]
    duration: Optional[float]
    cover_path: Optional[str]
    source: str
    external_id: Optional[str]
    cache_path: Optional[str]
    liked: bool = False
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
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    genre: Optional[str]
    year: Optional[int]
    track_number: Optional[int]
    duration: Optional[float]
    cover_path: Optional[str]
    source: str
    external_id: Optional[str]
    cache_path: Optional[str]
    liked: bool
    last_accessed: Optional[float]

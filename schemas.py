from pydantic import BaseModel
from typing import Optional

class TrackRead(BaseModel):
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

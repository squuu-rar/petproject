import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "tracks.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            title TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            year INTEGER,
            track_number INTEGER,
            duration REAL,
            cover_path TEXT,
            source TEXT DEFAULT 'local',
            external_id TEXT,
            cache_path TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(tracks)")
    columns = [col[1] for col in cur.fetchall()]
    migrations = [
        ("genre", "TEXT"),
        ("cover_path", "TEXT"),
        ("source", "TEXT DEFAULT 'local'"),
        ("external_id", "TEXT"),
        ("cache_path", "TEXT"),
    ]
    for col_name, col_type in migrations:
        if col_name not in columns:
            cur.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")
    conn.commit()
    conn.close()

def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves a single track by its ID."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_tracks_paginated(
    limit: int,
    offset: int,
    sort_by: str,
    order: str,
    source: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves a paginated list of tracks with optional filtering and sorting.
    """
    # Whitelist for security to prevent SQL injection in ORDER BY clause
    allowed_columns = {
        "id", "title", "artist", "album", "genre", 
        "year", "track_number", "duration", "source"
    }
    if sort_by not in allowed_columns:
        sort_by = "id"

    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    
    query = "SELECT * FROM tracks"
    params = []

    if source:
        query += " WHERE source = ?"
        params.append(source)

    query += f" ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()

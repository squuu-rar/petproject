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
            cache_path TEXT,
            liked INTEGER DEFAULT 0,
            last_accessed REAL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            event TEXT,
            elapsed_seconds REAL,
            timestamp REAL,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
        """
    )
    cur.execute("PRAGMA table_info(tracks)")
    columns = [col[1] for col in cur.fetchall()]
    migrations = [
        ("genre", "TEXT"),
        ("year", "INTEGER"),
        ("cover_path", "TEXT"),
        ("source", "TEXT DEFAULT 'local'"),
        ("external_id", "TEXT"),
        ("cache_path", "TEXT"),
        ("liked", "INTEGER DEFAULT 0"),
        ("last_accessed", "REAL DEFAULT 0"),
    ]
    for col_name, col_type in migrations:
        if col_name not in columns:
            cur.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")

    cur.execute("PRAGMA table_info(history)")
    hist_cols = [col[1] for col in cur.fetchall()]
    for col_name, col_type in [("event", "TEXT"), ("elapsed_seconds", "REAL"), ("timestamp", "REAL")]:
        if col_name not in hist_cols:
            cur.execute(f"ALTER TABLE history ADD COLUMN {col_name} {col_type}")

    conn.commit()
    conn.close()

def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
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
    source: Optional[str] = None,
    liked: Optional[bool] = None
) -> List[Dict[str, Any]]:
    allowed_columns = {
        "id", "title", "artist", "album", "genre", 
        "year", "track_number", "duration", "source"
    }
    if sort_by not in allowed_columns:
        sort_by = "id"

    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    
    query = "SELECT * FROM tracks"
    params = []
    conditions = []

    if source:
        conditions.append("source = ?")
        params.append(source)
    
    if liked is not None:
        conditions.append("liked = ?")
        params.append(1 if liked else 0)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += f" ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_playback_history(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        query = '''
            SELECT 
                h.id AS history_id,
                h.event,
                h.elapsed_seconds,
                h.timestamp,
                t.id,
                t.path,
                t.title,
                t.artist,
                t.album,
                t.genre,
                t.year,
                t.track_number,
                t.duration,
                t.cover_path,
                t.source,
                t.external_id,
                t.cache_path,
                t.liked,
                t.last_accessed
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE h.event IN ('play', 'finish')
            ORDER BY h.timestamp DESC
            LIMIT ?
        '''
        cursor = conn.execute(query, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def save_remote_track(track_data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db()
    try:
        cur = conn.cursor()
        ext_id = track_data.get("external_id")
        path = track_data.get("path")
        
        cur.execute(
            "SELECT * FROM tracks WHERE (external_id IS NOT NULL AND external_id = ?) OR path = ?",
            (ext_id, path)
        )
        row = cur.fetchone()
        if row:
            res = dict(row)
            res["liked"] = bool(res.get("liked", 0))
            return res

        cur.execute("""
            INSERT INTO tracks (
                path, title, artist, album, genre, year,
                track_number, duration, cover_path, source,
                external_id, cache_path, liked, last_accessed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'remote', ?, ?, 0, 0)
        """, (
            path,
            track_data.get("title"),
            track_data.get("artist"),
            track_data.get("album"),
            track_data.get("genre"),
            track_data.get("year"),
            track_data.get("track_number"),
            track_data.get("duration"),
            track_data.get("cover_path"),
            ext_id,
            track_data.get("cache_path")
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.execute("SELECT * FROM tracks WHERE id = ?", (new_id,))
        res = dict(cur.fetchone())
        res["liked"] = bool(res.get("liked", 0))
        return res
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()

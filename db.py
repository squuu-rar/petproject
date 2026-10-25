import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "tracks.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
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
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            event TEXT,
            elapsed_seconds REAL,
            timestamp REAL,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            playlist_id INTEGER,
            track_id INTEGER,
            position INTEGER,
            PRIMARY KEY (playlist_id, track_id),
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
            FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
        )
    """)
    cur.execute("PRAGMA table_info(tracks)")
    columns = [col[1] for col in cur.fetchall()]
    migrations = [
        ("genre", "TEXT"), ("year", "INTEGER"), ("cover_path", "TEXT"),
        ("source", "TEXT DEFAULT 'local'"), ("external_id", "TEXT"),
        ("cache_path", "TEXT"), ("liked", "INTEGER DEFAULT 0"),
        ("last_accessed", "REAL DEFAULT 0")
    ]
    for col_name, col_type in migrations:
        if col_name not in columns:
            cur.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")

    cur.execute("PRAGMA table_info(history)")
    hist_cols = [col[1] for col in cur.fetchall()]
    for col_name, col_type in [("event", "TEXT"), ("elapsed_seconds", "REAL"), ("timestamp", "REAL")]:
        if col_name not in hist_cols:
            cur.execute(f"ALTER TABLE history ADD COLUMN {col_name} {col_type}")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_external_id ON tracks(external_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_liked ON tracks(liked)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_track ON history(track_id)")

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
    allowed = {"id", "title", "artist", "album", "genre", "year", "track_number", "duration", "source"}
    if sort_by not in allowed:
        sort_by = "id"
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    
    query = "SELECT * FROM tracks"
    params = []
    conds = []
    if source:
        conds.append("source = ?")
        params.append(source)
    if liked is not None:
        conds.append("liked = ?")
        params.append(1 if liked else 0)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += f" ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        cur = conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def get_playback_history(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        query = """
            SELECT 
                h.id AS history_id, h.event, h.elapsed_seconds, h.timestamp,
                t.id, t.path, t.title, t.artist, t.album, t.genre, t.year,
                t.track_number, t.duration, t.cover_path, t.source, t.external_id,
                t.cache_path, t.liked, t.last_accessed
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE h.event IN ('play', 'finish')
            ORDER BY h.timestamp DESC LIMIT ?
        """
        cur = conn.execute(query, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def get_wave_exclusions(days: int = 3, skip_threshold: int = 2) -> set:
    since = time.time() - days * 86400
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT t.external_id
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE t.external_id IS NOT NULL
            GROUP BY t.external_id
            HAVING SUM(CASE WHEN h.event IN ('play', 'finish') AND h.timestamp >= ? THEN 1 ELSE 0 END) > 0
               OR SUM(CASE WHEN h.event = 'skip' THEN 1 ELSE 0 END) >= ?
        """, (since, skip_threshold)).fetchall()
        return {r["external_id"] for r in rows}
    finally:
        conn.close()

def save_remote_track(track_data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db()
    try:
        cur = conn.cursor()
        ext_id = track_data.get("external_id")
        path = track_data.get("path")
        source = track_data.get("source") or "remote"
        
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            path, track_data.get("title"), track_data.get("artist"),
            track_data.get("album"), track_data.get("genre"), track_data.get("year"),
            track_data.get("track_number"), track_data.get("duration"),
            track_data.get("cover_path"), source, ext_id, track_data.get("cache_path")
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.execute("SELECT * FROM tracks WHERE id = ?", (new_id,))
        res = dict(cur.fetchone())
        res["liked"] = bool(res.get("liked", 0))
        return res
    finally:
        conn.close()

def get_all_tracks_with_play_counts() -> List[Dict[str, Any]]:
    """Returns all tracks with their play count from history."""
    conn = get_db()
    try:
        query = """
            SELECT t.*, COUNT(h.id) as play_count
            FROM tracks t
            LEFT JOIN history h ON t.id = h.track_id AND h.event IN ('play', 'finish')
            GROUP BY t.id
        """
        cur = conn.execute(query)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()


def init_playlists_tables():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
            )
        """)
        conn.commit()
    finally:
        conn.close()

def create_playlist(name: str) -> dict:
    init_playlists_tables()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
        playlist_id = cursor.lastrowid
        conn.commit()
        return {"id": playlist_id, "name": name, "track_count": 0}
    finally:
        conn.close()

def get_playlists() -> list:
    init_playlists_tables()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.name, COUNT(pt.track_id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            GROUP BY p.id, p.name
            ORDER BY p.id DESC
        """)
        rows = cursor.fetchall()
        return [
            {"id": row["id"] if isinstance(row, dict) else row[0],
             "name": row["name"] if isinstance(row, dict) else row[1],
             "track_count": row["track_count"] if isinstance(row, dict) else row[2]}
            for row in rows
        ]
    finally:
        conn.close()


def get_or_create_playlist(name: str) -> dict:
    clean_name = (name or "Мой плейлист").strip()
    init_playlists_tables()
    conn = get_db()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT id, name FROM playlists WHERE LOWER(name) = LOWER(?)", (clean_name,)).fetchone()
        if row:
            pid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            pname = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            cnt_row = cur.execute("SELECT COUNT(track_id) FROM playlist_tracks WHERE playlist_id = ?", (pid,)).fetchone()
            cnt = cnt_row[0] if cnt_row else 0
            return {"id": pid, "name": pname, "track_count": cnt}
        cur.execute("INSERT INTO playlists (name) VALUES (?)", (clean_name,))
        pid = cur.lastrowid
        conn.commit()
        return {"id": pid, "name": clean_name, "track_count": 0}
    finally:
        conn.close()


def add_tracks_to_playlist(playlist_id: int, track_ids: List[int]) -> int:
    if not track_ids:
        return 0
    init_playlists_tables()
    conn = get_db()
    try:
        cur = conn.cursor()
        max_pos_row = cur.execute(
            "SELECT COALESCE(MAX(position), 0) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,)
        ).fetchone()
        pos = int(max_pos_row[0]) if max_pos_row else 0
        added = 0
        for tid in track_ids:
            pos += 1
            cur.execute(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                (playlist_id, tid, pos)
            )
            added += cur.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


def get_playlist_tracks(playlist_id: int, limit: int = 5000, offset: int = 0) -> List[Dict[str, Any]]:
    init_playlists_tables()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT t.*
            FROM playlist_tracks pt
            JOIN tracks t ON pt.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC, pt.rowid ASC
            LIMIT ? OFFSET ?
        """, (playlist_id, limit, offset)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["liked"] = bool(d.get("liked", 0))
            out.append(d)
        return out
    finally:
        conn.close()


def bulk_import_tracks(
    tracks_data: List[Dict[str, Any]],
    target_mode: str = "both",
    playlist_name: Optional[str] = "Импорт из Яндекс Музыки"
) -> Dict[str, Any]:
    """Пакетно сохраняет 1000+ треков за одну транзакцию и привязывает к Любимым и/или плейлисту."""
    init_playlists_tables()
    mark_liked = 1 if target_mode in ("favorites", "both") else 0
    use_playlist = target_mode in ("playlist", "both")

    playlist_info = None
    if use_playlist:
        playlist_info = get_or_create_playlist(playlist_name or "Импорт из Яндекс Музыки")

    conn = get_db()
    track_ids: List[int] = []
    imported_count = 0
    try:
        cur = conn.cursor()
        for item in tracks_data:
            path = item.get("path")
            if not path:
                continue
            existing = cur.execute("SELECT id, liked FROM tracks WHERE path = ?", (path,)).fetchone()
            if existing:
                tid = existing["id"]
                new_liked = 1 if (existing["liked"] or mark_liked) else 0
                cur.execute("""
                    UPDATE tracks
                    SET title = COALESCE(?, title),
                        artist = COALESCE(?, artist),
                        album = COALESCE(?, album),
                        duration = COALESCE(?, duration),
                        cover_path = COALESCE(?, cover_path),
                        liked = ?
                    WHERE id = ?
                """, (
                    item.get("title"), item.get("artist"), item.get("album"),
                    item.get("duration"), item.get("cover_path"), new_liked, tid
                ))
            else:
                cur.execute("""
                    INSERT INTO tracks (
                        path, title, artist, album, genre, year,
                        track_number, duration, cover_path, source,
                        external_id, cache_path, liked, last_accessed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', NULL, NULL, ?, ?)
                """, (
                    path,
                    item.get("title") or "Без названия",
                    item.get("artist") or "Неизвестный исполнитель",
                    item.get("album") or "-",
                    item.get("genre"),
                    item.get("year"),
                    item.get("track_number"),
                    item.get("duration"),
                    item.get("cover_path"),
                    mark_liked,
                    time.time()
                ))
                tid = cur.lastrowid
                imported_count += 1
            track_ids.append(int(tid))

        if use_playlist and playlist_info and track_ids:
            pid = playlist_info["id"]
            max_pos_row = cur.execute(
                "SELECT COALESCE(MAX(position), 0) FROM playlist_tracks WHERE playlist_id = ?",
                (pid,)
            ).fetchone()
            pos = int(max_pos_row[0]) if max_pos_row else 0
            for tid in track_ids:
                pos += 1
                cur.execute(
                    "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                    (pid, tid, pos)
                )

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "imported_count": len(track_ids),
        "new_inserted": imported_count,
        "total_scanned": len(tracks_data),
        "target_mode": target_mode,
        "playlist_id": playlist_info["id"] if playlist_info else None,
        "playlist_name": playlist_info["name"] if playlist_info else None,
    }


def get_taste_profile_artists(limit: int = 30) -> List[str]:
    """Возвращает топ любимых артистов с учётом:
    1) Треков в 'Мне нравится' (liked = 1)
    2) Всех треков в пользовательских плейлистах (включая импортированные 1000+ треков)
    3) Истории прослушиваний (play / finish)
    """
    init_playlists_tables()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT artist, SUM(weight) as score
            FROM (
                SELECT artist, 3.0 as weight FROM tracks WHERE liked = 1 AND artist IS NOT NULL
                UNION ALL
                SELECT t.artist, 2.5 as weight
                FROM playlist_tracks pt
                JOIN tracks t ON pt.track_id = t.id
                WHERE t.artist IS NOT NULL
                UNION ALL
                SELECT t.artist, 2.0 as weight
                FROM history h
                JOIN tracks t ON h.track_id = t.id
                WHERE h.event IN ('play', 'finish') AND t.artist IS NOT NULL
            )
            WHERE TRIM(artist) != ''
              AND LOWER(TRIM(artist)) NOT IN ('неизвестный исполнитель', 'unknown artist', 'unknown', '-')
            GROUP BY LOWER(TRIM(artist))
            ORDER BY score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [r["artist"] for r in rows if r["artist"]]
    finally:
        conn.close()

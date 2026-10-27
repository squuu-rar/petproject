import random
import re
import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "tracks.db"

INVALID_ARTIST_NAMES = {
    "",
    "-",
    "unknown",
    "unknown artist",
    "неизвестный исполнитель",
    "неизвестен",
    "various artists",
    "разные исполнители",
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
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
            FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
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
            playlist_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            PRIMARY KEY (playlist_id, track_id),
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
            FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
        )
    """)
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

    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_external_id ON tracks(external_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_liked ON tracks(liked)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_source ON tracks(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_track ON history(track_id)")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_tracks_unique ON playlist_tracks(playlist_id, track_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_playlist_tracks_pid ON playlist_tracks(playlist_id, position)"
    )

    conn.commit()
    conn.close()


def _normalize_track_row(row: Any) -> Dict[str, Any]:
    d = dict(row)
    d["liked"] = bool(d.get("liked", 0))
    return d


def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return _normalize_track_row(row) if row else None
    finally:
        conn.close()


def get_tracks_paginated(
    limit: int,
    offset: int,
    sort_by: str,
    order: str,
    source: Optional[str] = None,
    liked: Optional[bool] = None,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    allowed = {"id", "title", "artist", "album", "genre", "year", "track_number", "duration", "source", "last_accessed"}
    if sort_by not in allowed:
        sort_by = "id"
    order_sql = "DESC" if order.lower() == "desc" else "ASC"

    query = "SELECT * FROM tracks"
    params: List[Any] = []
    conds: List[str] = []
    if source:
        conds.append("source = ?")
        params.append(source)
    if liked is not None:
        conds.append("liked = ?")
        params.append(1 if liked else 0)
    if search and search.strip():
        q = f"%{search.strip()}%"
        conds.append("(title LIKE ? OR artist LIKE ? OR album LIKE ?)")
        params.extend([q, q, q])

    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += f" ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        cur = conn.execute(query, params)
        return [_normalize_track_row(row) for row in cur.fetchall()]
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
        return [_normalize_track_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_wave_exclusions(days: int = 3, skip_threshold: int = 1) -> set:
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


def get_known_collection_keys() -> set[tuple[str, str]]:
    """Возвращает множество пар (artist_lower, title_lower) для всех треков, которые уже есть
    в «Любимом» или в плейлистах пользователя, чтобы не дублировать их во внешнем пуле рекомендаций.
    """
    init_playlists_tables()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT artist, title FROM tracks
            WHERE liked = 1
               OR source = 'local'
               OR id IN (SELECT track_id FROM playlist_tracks)
        """).fetchall()
        out: set[tuple[str, str]] = set()
        for r in rows:
            a = (r["artist"] or "").strip().lower()
            t = (r["title"] or "").strip().lower()
            if t:
                out.add((a, t))
        return out
    finally:
        conn.close()


def get_recently_played_or_skipped_ids(hours: float = 24.0) -> set[int]:
    """Возвращает ID треков, которые уже играли или были пропущены за последние N часов."""
    since = time.time() - hours * 3600.0
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT track_id FROM history WHERE timestamp >= ? AND track_id IS NOT NULL",
            (since,),
        ).fetchall()
        return {int(r["track_id"]) for r in rows if r["track_id"] is not None}
    finally:
        conn.close()


def get_recently_skipped_artists(days: float = 2.0) -> set[str]:
    """Возвращает список артистов в нижнем регистре, которых пользователь недавно скипал («Не моё»)."""
    since = time.time() - days * 86400.0
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT LOWER(TRIM(t.artist)) as art,
                   SUM(CASE WHEN h.event = 'skip' THEN 1 ELSE 0 END) as skips,
                   SUM(CASE WHEN h.event = 'finish' THEN 1 ELSE 0 END) as finishes
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE h.timestamp >= ? AND t.artist IS NOT NULL
            GROUP BY LOWER(TRIM(t.artist))
            HAVING skips > finishes AND skips >= 1
        """, (since,)).fetchall()
        return {r["art"] for r in rows if r["art"]}
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
            (ext_id, path),
        )
        row = cur.fetchone()
        if row:
            return _normalize_track_row(row)

        cur.execute("""
            INSERT INTO tracks (
                path, title, artist, album, genre, year,
                track_number, duration, cover_path, source,
                external_id, cache_path, liked, last_accessed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
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
            source,
            ext_id,
            track_data.get("cache_path"),
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.execute("SELECT * FROM tracks WHERE id = ?", (new_id,))
        return _normalize_track_row(cur.fetchone())
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
        return [_normalize_track_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


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
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY (playlist_id, track_id),
                FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
                FOREIGN KEY (track_id) REFERENCES tracks (id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_tracks_unique ON playlist_tracks(playlist_id, track_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_playlist_tracks_pid ON playlist_tracks(playlist_id, position)"
        )
        conn.commit()
    finally:
        conn.close()


def create_playlist(name: str) -> dict:
    clean_name = (name or "Мой плейлист").strip()
    init_playlists_tables()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO playlists (name) VALUES (?)", (clean_name,))
        playlist_id = cursor.lastrowid
        conn.commit()
        return {"id": playlist_id, "name": clean_name, "track_count": 0, "cover_path": None}
    finally:
        conn.close()


def get_playlists() -> list:
    init_playlists_tables()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                p.id,
                p.name,
                COUNT(pt.track_id) as track_count,
                (
                    SELECT t.cover_path
                    FROM playlist_tracks pt2
                    JOIN tracks t ON pt2.track_id = t.id
                    WHERE pt2.playlist_id = p.id AND t.cover_path IS NOT NULL
                    ORDER BY pt2.position ASC
                    LIMIT 1
                ) as cover_path
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            GROUP BY p.id, p.name
            ORDER BY p.id DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                "id": row["id"] if isinstance(row, sqlite3.Row) else row[0],
                "name": row["name"] if isinstance(row, sqlite3.Row) else row[1],
                "track_count": row["track_count"] if isinstance(row, sqlite3.Row) else row[2],
                "cover_path": row["cover_path"] if isinstance(row, sqlite3.Row) else row[3],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_playlist_by_id(playlist_id: int) -> Optional[Dict[str, Any]]:
    init_playlists_tables()
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT p.id, p.name, COUNT(pt.track_id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.id = ?
            GROUP BY p.id, p.name
            """,
            (playlist_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_or_create_playlist(name: str) -> dict:
    clean_name = (name or "Импорт из Яндекс Музыки").strip()
    init_playlists_tables()
    conn = get_db()
    try:
        cur = conn.cursor()
        existing = cur.execute(
            """
            SELECT p.id, p.name, COUNT(pt.track_id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE LOWER(TRIM(p.name)) = LOWER(TRIM(?))
            GROUP BY p.id, p.name
            ORDER BY p.id DESC
            LIMIT 1
            """,
            (clean_name,),
        ).fetchone()
        if existing:
            return {
                "id": existing["id"],
                "name": existing["name"],
                "track_count": existing["track_count"],
            }

        cur.execute("INSERT INTO playlists (name) VALUES (?)", (clean_name,))
        pid = cur.lastrowid
        conn.commit()
        return {"id": pid, "name": clean_name, "track_count": 0}
    finally:
        conn.close()


def rename_playlist(playlist_id: int, new_name: str) -> Optional[Dict[str, Any]]:
    clean_name = (new_name or "").strip()
    if not clean_name:
        return None
    init_playlists_tables()
    conn = get_db()
    try:
        conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (clean_name, playlist_id))
        conn.commit()
        return get_playlist_by_id(playlist_id)
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
            (playlist_id,),
        ).fetchone()
        pos = int(max_pos_row[0]) if max_pos_row else 0
        added = 0
        for tid in track_ids:
            pos += 1
            cur.execute(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                (playlist_id, tid, pos),
            )
            added += cur.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


def remove_track_from_playlist(playlist_id: int, track_id: int) -> bool:
    init_playlists_tables()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        conn.commit()
        return cur.rowcount > 0
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
        return [_normalize_track_row(r) for r in rows]
    finally:
        conn.close()


def bulk_import_tracks(
    tracks_data: List[Dict[str, Any]],
    target_mode: str = "both",
    playlist_name: Optional[str] = "Импорт из Яндекс Музыки",
    playlist_id: Optional[int] = None,
) -> Dict[str, Any]:
    init_db()
    init_playlists_tables()

    if target_mode not in ("favorites", "playlist", "both", "library"):
        target_mode = "both"

    mark_liked = 1 if target_mode in ("favorites", "both") else 0
    use_playlist = target_mode in ("playlist", "both")

    playlist_info = None
    if use_playlist:
        if playlist_id is not None:
            playlist_info = get_playlist_by_id(playlist_id)
        if not playlist_info:
            playlist_info = get_or_create_playlist(playlist_name or "Импорт из Яндекс Музыки")

    conn = get_db()
    track_ids: List[int] = []
    new_inserted = 0
    updated_existing = 0
    now = time.time()

    try:
        cur = conn.cursor()
        existing_rows = cur.execute("SELECT id, path, liked FROM tracks WHERE path IS NOT NULL").fetchall()
        existing_by_path: Dict[str, tuple[int, int]] = {
            r["path"]: (int(r["id"]), int(r["liked"] or 0)) for r in existing_rows
        }
        newly_liked_ids: List[int] = []

        for item in tracks_data:
            path = item.get("path")
            if not path:
                continue
            if path in existing_by_path:
                tid, old_liked = existing_by_path[path]
                new_liked = 1 if (old_liked or mark_liked) else 0
                cur.execute("""
                    UPDATE tracks
                    SET title = COALESCE(?, title),
                        artist = COALESCE(?, artist),
                        album = COALESCE(?, album),
                        genre = COALESCE(?, genre),
                        year = COALESCE(?, year),
                        track_number = COALESCE(?, track_number),
                        duration = COALESCE(?, duration),
                        cover_path = COALESCE(?, cover_path),
                        liked = ?,
                        last_accessed = ?
                    WHERE id = ?
                """, (
                    item.get("title"),
                    item.get("artist"),
                    item.get("album"),
                    item.get("genre"),
                    item.get("year"),
                    item.get("track_number"),
                    item.get("duration"),
                    item.get("cover_path"),
                    new_liked,
                    now,
                    tid,
                ))
                existing_by_path[path] = (tid, new_liked)
                if mark_liked and not old_liked:
                    newly_liked_ids.append(tid)
                updated_existing += 1
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
                    now,
                ))
                tid = int(cur.lastrowid)
                existing_by_path[path] = (tid, mark_liked)
                if mark_liked:
                    newly_liked_ids.append(tid)
                new_inserted += 1
            track_ids.append(int(tid))

        if newly_liked_ids:
            cur.executemany(
                "INSERT INTO history (track_id, event, elapsed_seconds, timestamp) VALUES (?, 'like', 0.0, ?)",
                [(tid, now) for tid in newly_liked_ids],
            )

        if use_playlist and playlist_info and track_ids:
            pid = playlist_info["id"]
            max_pos_row = cur.execute(
                "SELECT COALESCE(MAX(position), 0) FROM playlist_tracks WHERE playlist_id = ?",
                (pid,),
            ).fetchone()
            pos = int(max_pos_row[0]) if max_pos_row else 0
            pl_rows = []
            for tid in track_ids:
                pos += 1
                pl_rows.append((pid, tid, pos))
            cur.executemany(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                pl_rows,
            )

        conn.commit()
    finally:
        conn.close()

    taste_artists = get_taste_profile_artists(limit=100)

    return {
        "status": "ok",
        "imported_count": len(track_ids),
        "new_inserted": new_inserted,
        "updated_existing": updated_existing,
        "total_scanned": len(tracks_data),
        "target_mode": target_mode,
        "playlist_id": playlist_info["id"] if playlist_info else None,
        "playlist_name": playlist_info["name"] if playlist_info else None,
        "taste_artists_count": len(taste_artists),
    }


def _split_artists(raw_artist: Optional[str]) -> List[str]:
    if not raw_artist:
        return []
    cleaned = raw_artist.strip()
    if cleaned.lower() in INVALID_ARTIST_NAMES:
        return []
    parts = re.split(r"\s*(?:,|;|/|\bfeat\.?\b|\bft\.?\b|\bпри уч\.?\b)\s*", cleaned, flags=re.IGNORECASE)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if p and p.lower() not in INVALID_ARTIST_NAMES:
            out.append(p)
    return out or [cleaned]


def get_taste_profile_artists(limit: int = 30) -> List[str]:
    init_playlists_tables()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT artist, SUM(weight) as score
            FROM (
                SELECT artist, 3.5 as weight FROM tracks WHERE liked = 1 AND artist IS NOT NULL
                UNION ALL
                SELECT t.artist, 2.5 as weight
                FROM playlist_tracks pt
                JOIN tracks t ON pt.track_id = t.id
                WHERE t.artist IS NOT NULL
                UNION ALL
                SELECT t.artist,
                       CASE
                           WHEN h.event = 'finish' THEN 3.0
                           WHEN h.event = 'play' THEN 1.5
                           WHEN h.event = 'like' THEN 2.0
                           WHEN h.event = 'skip' THEN -2.5
                           ELSE 0.0
                       END as weight
                FROM history h
                JOIN tracks t ON h.track_id = t.id
                WHERE t.artist IS NOT NULL
                UNION ALL
                SELECT artist, 1.0 as weight FROM tracks WHERE source = 'local' AND artist IS NOT NULL
            )
            WHERE TRIM(artist) != ''
              AND LOWER(TRIM(artist)) NOT IN ('неизвестный исполнитель', 'unknown artist', 'unknown', '-')
            GROUP BY LOWER(TRIM(artist))
            HAVING score > 0
            ORDER BY score DESC
            LIMIT ?
        """, (limit * 2,)).fetchall()

        seen_lower: set[str] = set()
        result: List[str] = []
        for r in rows:
            raw_a = r["artist"]
            if not raw_a:
                continue
            for cand in [raw_a.strip()] + _split_artists(raw_a):
                key = cand.lower()
                if key not in INVALID_ARTIST_NAMES and key not in seen_lower:
                    seen_lower.add(key)
                    result.append(cand)
                    if len(result) >= limit:
                        return result
        return result
    finally:
        conn.close()


def sample_taste_tracks(
    limit: int = 20,
    max_per_artist: int = 1,
    exclude_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Быстрая взвешенная выборка треков из коллекции с исключением недавно прослушанных/пропущенных."""
    init_playlists_tables()
    recent_blocked = get_recently_played_or_skipped_ids(hours=12.0)
    skipped_artists = get_recently_skipped_artists(days=2.0)
    exclude = (exclude_ids or set()) | recent_blocked

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                t.id,
                t.artist,
                t.liked,
                CASE WHEN EXISTS (
                    SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id
                ) THEN 1 ELSE 0 END as in_playlist
            FROM tracks t
            WHERE (t.liked = 1 OR t.source = 'local' OR EXISTS (
                SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id
            ))
              AND t.title IS NOT NULL
              AND LENGTH(TRIM(t.title)) > 1
        """).fetchall()

        if not rows:
            return []

        candidates = [
            dict(r) for r in rows
            if r["id"] not in exclude
            and str(r["artist"] or "").strip().lower() not in skipped_artists
        ]
        if not candidates:
            candidates = [dict(r) for r in rows if r["id"] not in (exclude_ids or set())]
        if not candidates:
            candidates = [dict(r) for r in rows]

        weighted: List[tuple[float, Dict[str, Any]]] = []
        for c in candidates:
            base_w = 1.0
            if c.get("liked"):
                base_w += 2.0
            if c.get("in_playlist"):
                base_w += 1.5
            score = random.random() ** (1.0 / base_w)
            weighted.append((score, c))

        weighted.sort(key=lambda x: x[0], reverse=True)

        selected_ids: List[int] = []
        artist_counts: Dict[str, int] = {}
        for _, c in weighted:
            art_key = str(c.get("artist") or "").strip().lower()
            if art_key and art_key not in INVALID_ARTIST_NAMES:
                if artist_counts.get(art_key, 0) >= max_per_artist:
                    continue
                artist_counts[art_key] = artist_counts.get(art_key, 0) + 1
            selected_ids.append(int(c["id"]))
            if len(selected_ids) >= limit:
                break

        if len(selected_ids) < limit:
            chosen_set = set(selected_ids)
            for _, c in weighted:
                tid = int(c["id"])
                if tid not in chosen_set:
                    selected_ids.append(tid)
                    chosen_set.add(tid)
                    if len(selected_ids) >= limit:
                        break

        if not selected_ids:
            return []

        placeholders = ",".join(["?"] * len(selected_ids))
        full_rows = conn.execute(
            f"SELECT * FROM tracks WHERE id IN ({placeholders})",
            selected_ids,
        ).fetchall()
        by_id = {int(r["id"]): _normalize_track_row(r) for r in full_rows}
        return [by_id[tid] for tid in selected_ids if tid in by_id]
    finally:
        conn.close()


def delete_playlist(playlist_id: int) -> bool:
    init_playlists_tables()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        cur.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    init_playlists_tables()

import sqlite3
from pathlib import Path

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

if __name__ == "__main__":
    init_db()

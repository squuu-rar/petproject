"""Listening signals and playback-based metrics. No external telemetry."""
import time
from collections import Counter

VERSION = "wave-v3"


def signal_weight(event, elapsed=0, duration=None):
    ratio = min(1.0, elapsed / duration) if duration and duration > 0 else None
    if event == "like":
        return 3.5
    if event == "dislike":
        return -4.0
    if event in ("error", "play"):
        return 0.0
    if ratio is not None and ratio >= 0.8:
        return 2.5
    if event == "skip":
        if elapsed < 15 or (ratio is not None and ratio < 0.25):
            return -3.0
        return -1.0 if ratio is None or ratio < 0.5 else 0.0
    if event == "finish":
        return 2.5 if ratio is None else (1.0 if ratio >= 0.5 else 0.0)
    return 0.0


def migrate(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    for name, sql_type in {
        "duration_seconds": "REAL", "listen_ratio": "REAL", "signal_weight": "REAL",
        "playback_id": "TEXT", "context": "TEXT", "is_discovery": "INTEGER",
        "recommendation_version": "TEXT",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE history ADD COLUMN {name} {sql_type}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_event ON history(playback_id, event) WHERE playback_id IS NOT NULL")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_history_terminal ON history(playback_id)
                    WHERE playback_id IS NOT NULL AND event IN ('skip','finish','error','dislike','stop')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS music_tag_cache (
        artist TEXT NOT NULL, title TEXT NOT NULL, tags TEXT NOT NULL,
        expires_at REAL NOT NULL, PRIMARY KEY (artist, title))""")


def record_event(conn, track, payload):
    duration = payload.duration_seconds or track.get("duration")
    elapsed = min(payload.elapsed_seconds, duration) if duration and duration > 0 else payload.elapsed_seconds
    ratio = min(1.0, elapsed / duration) if duration and duration > 0 else None
    if payload.playback_id:
        previous = conn.execute("SELECT track_id FROM history WHERE playback_id=? LIMIT 1", (payload.playback_id,)).fetchone()
        if previous and previous[0] != track["id"]:
            raise ValueError("playback_id belongs to another track")
    conn.execute("""INSERT OR IGNORE INTO history
        (track_id,event,elapsed_seconds,timestamp,duration_seconds,listen_ratio,signal_weight,
         playback_id,context,is_discovery,recommendation_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
        track["id"], payload.event, elapsed, time.time(), duration, ratio,
        signal_weight(payload.event, elapsed, duration), payload.playback_id, payload.context,
        int(payload.is_discovery), payload.recommendation_version,
    ))


def quality_metrics(days=30):
    from db import get_db
    conn = get_db()
    try:
        rows = conn.execute("""SELECT h.*, t.artist FROM history h JOIN tracks t ON t.id=h.track_id
            WHERE h.timestamp>=? AND h.context='wave' AND h.playback_id IS NOT NULL
            ORDER BY h.timestamp, h.id""", (time.time() - days * 86400,)).fetchall()
    finally:
        conn.close()
    sessions = {}
    for row in rows:
        sessions.setdefault(row["playback_id"], {})[row["event"]] = dict(row)
    groups = {"all": list(sessions.values())}
    for events in sessions.values():
        sample = events.get("play") or next(iter(events.values()))
        version = sample.get("recommendation_version") or "unknown"
        groups.setdefault(version, []).append(events)

    def summarize(items):
        starts = [e for e in items if "play" in e]
        completed = early = errors = discovered = liked_new = settled = 0
        ratios = []
        artists = []
        for events in starts:
            play = events["play"]
            artists.append((play["artist"] or "").strip().lower())
            discovered += bool(play["is_discovery"])
            liked_new += bool(play["is_discovery"] and "like" in events)
            terminal = next((events[k] for k in ("finish", "skip", "dislike", "error", "stop") if k in events), None)
            if not terminal:
                continue
            settled += 1
            errors += terminal["event"] == "error"
            completed += terminal["event"] != "error" and (terminal["listen_ratio"] or 0) >= 0.8
            early += terminal["event"] in ("skip", "dislike") and (
                terminal["elapsed_seconds"] < 15 or
                (terminal["listen_ratio"] is not None and terminal["listen_ratio"] < 0.25))
            if terminal["listen_ratio"] is not None and terminal["event"] != "error":
                ratios.append(terminal["listen_ratio"])
        pairs = max(0, len(artists) - 1)
        repeats = sum(a == b for a, b in zip(artists, artists[1:]) if a)
        rate = lambda value, total: round(value / total, 4) if total else None
        counts = Counter(a for a in artists if a)
        return {"plays": len(starts), "settled_plays": settled, "completed": completed,
                "early_skips": early, "errors": errors, "discovery_plays": discovered,
                "liked_discoveries": liked_new, "completion_rate": rate(completed, settled - errors),
                "early_skip_rate": rate(early, settled - errors), "error_rate": rate(errors, settled),
                "discovery_like_rate": rate(liked_new, discovered),
                "mean_listen_ratio": rate(sum(ratios), len(ratios)),
                "adjacent_artist_repeat_rate": rate(repeats, pairs),
                "unique_artists": len(counts), "top_artist_share": rate(max(counts.values(), default=0), len(artists))}
    return {"days": days, "algorithm_version": VERSION, "overall": summarize(groups.pop("all")),
            "by_version": {key: summarize(value) for key, value in groups.items()}}

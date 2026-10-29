"""Canonical queue identities and persistent Last.fm metadata cache."""
import asyncio
import json
import time


def normalize(value):
    return " ".join(str(value or "").lower().split())


def track_key(track):
    return normalize(track.get("artist")), normalize(track.get("title"))


class Exclusions:
    def __init__(self, tracks=()):
        self.ids = {t["id"] for t in tracks if isinstance(t.get("id"), int) and t["id"] > 0}
        self.external_ids = {t["external_id"] for t in tracks if t.get("external_id")}
        self.keys = {track_key(t) for t in tracks if all(track_key(t))}

    def contains(self, track):
        return (track.get("id") in self.ids or track.get("external_id") in self.external_ids
                or track_key(track) in self.keys)


LANGUAGE_TAGS = {
    "ru": {"russian language", "russian-language", "russian lyrics", "на русском"},
    "foreign": {"english language", "english lyrics", "english-language", "french language",
                "german language", "japanese language", "spanish language", "korean language"},
    "instrumental": {"instrumental", "instrumental music", "без слов"},
}


def apply_tags(track, track_tags, artist_tags):
    result = dict(track)
    result["tags"] = list(dict.fromkeys(track_tags + artist_tags))
    # An artist's country/genre cannot establish the language of a particular song.
    languages = [lang for lang, tags in LANGUAGE_TAGS.items() if tags.intersection(track_tags)]
    result["detected_language"] = languages[0] if len(languages) == 1 else None
    return result


def cached_tags(tracks, conn=None):
    from db import get_db
    own = conn is None
    conn = conn or get_db()
    try:
        cache = {}
        visited = set()
        for track in tracks:
            artist, title = track_key(track)
            for key in ((artist, title), (artist, "")):
                if key in visited:
                    continue
                visited.add(key)
                row = conn.execute("SELECT tags, expires_at FROM music_tag_cache WHERE artist=? AND title=?", key).fetchone()
                if row and row["expires_at"] > time.time():
                    try:
                        cache[key] = json.loads(row["tags"])
                    except (ValueError, TypeError):
                        pass
        return cache
    finally:
        if own:
            conn.close()


def with_cached_tags(tracks, conn=None):
    cache = cached_tags(tracks, conn)
    return [apply_tags(t, cache.get(track_key(t), []), cache.get((track_key(t)[0], ""), [])) for t in tracks]


async def enrich_tracks(tracks, service, offline=False):
    from db import get_db
    cache = await asyncio.to_thread(cached_tags, tracks)
    missing = {}
    if not offline and getattr(service, "api_key", None):
        for track in tracks:
            artist, title = track_key(track)
            if not artist:
                continue
            for key, raw_title in (((artist, title), track.get("title")), ((artist, ""), None)):
                if key not in cache:
                    missing.setdefault(key, (track["artist"], raw_title))
    semaphore = asyncio.Semaphore(4)

    async def fetch(key, args):
        async with semaphore:
            try:
                tags = await asyncio.to_thread(service.get_top_tags, *args)
            except Exception:
                return
            # None denotes a transport/API error; short backoff, not a month-long negative cache.
            ttl = 300 if tags is None else (30 * 86400 if tags else 86400)
            tags = tags or []
            def save():
                conn = get_db()
                try:
                    conn.execute("INSERT OR REPLACE INTO music_tag_cache VALUES (?,?,?,?)",
                                 (*key, json.dumps(tags), time.time() + ttl))
                    conn.commit()
                finally:
                    conn.close()
            await asyncio.to_thread(save)
            cache[key] = tags

    # Bound extra API traffic per batch. Unenriched tracks remain eligible.
    await asyncio.gather(*(fetch(key, args) for key, args in list(missing.items())[:16]))
    return [apply_tags(t, cache.get(track_key(t), []), cache.get((track_key(t)[0], ""), [])) for t in tracks]

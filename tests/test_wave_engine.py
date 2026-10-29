import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import db
import wave_engine
from wave_engine import WaveEngine


def track(number, artist=None, **kwargs):
    return {"id": number, "artist": artist or f"Artist {number}", "title": f"Song {number}",
            "external_id": f"video-{number}", "duration": 200, "source": "remote", **kwargs}


def insert_track(number, **kwargs):
    item = track(number, **kwargs)
    item.setdefault("path", f"path-{number}")
    conn = db.get_db()
    try:
        conn.execute(f"INSERT INTO tracks ({','.join(item)}) VALUES ({','.join('?' for _ in item)})", tuple(item.values()))
        conn.commit()
    finally:
        conn.close()


def event(number, kind, age=0):
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO history (track_id, event, timestamp) VALUES (?, ?, ?)",
                     (number, kind, time.time() - age))
        conn.commit()
    finally:
        conn.close()


def test_old_skip_expires(temp_db):
    insert_track(1)
    insert_track(2)
    event(1, "skip", 4 * 86400)
    event(2, "skip")
    assert db.get_wave_exclusions() == {"video-2"}


def test_artist_requires_repeated_skips_and_handles_cyrillic(temp_db):
    insert_track(1, artist="КИНО")
    insert_track(2, artist="кино")
    event(1, "skip")
    assert db.get_recently_skipped_artists() == set()
    assert db.get_realtime_session_signals()["skipped_artists"] == set()
    event(2, "skip")
    assert db.get_recently_skipped_artists() == {"кино"}
    assert db.get_realtime_session_signals()["skipped_artists"] == {"кино"}


def test_latest_skip_overrides_positive_seed(temp_db):
    insert_track(1)
    event(1, "finish", 10)
    event(1, "skip")
    assert db.get_realtime_session_signals()["positive_track_ids"] == []


def test_autoplay_does_not_create_taste(temp_db):
    insert_track(1)
    event(1, "play")
    assert db.get_taste_profile_artists() == []


def test_recent_positive_outweighs_old_positive(temp_db):
    insert_track(1)
    insert_track(2)
    event(1, "finish", 365 * 86400)
    event(2, "finish")
    assert db.get_taste_profile_artists() == ["Artist 2", "Artist 1"]


@pytest.mark.parametrize("options", [{"offline_only": True}, {"language": "ru"}, {"exclude_ids": {1}}])
def test_sampling_never_relaxes_hard_filters(temp_db, options):
    insert_track(1, liked=1)
    assert db.sample_taste_tracks(**options) == []


def test_sampling_can_relax_play_cooldown_but_not_skips(temp_db, tmp_path):
    audio = tmp_path / "song.mp3"
    audio.touch()
    insert_track(1, source="local", path=str(audio))
    insert_track(2, liked=1)
    event(1, "finish")
    event(2, "skip")
    assert [t["id"] for t in db.sample_taste_tracks()] == [1]
    assert [t["id"] for t in db.sample_taste_tracks(offline_only=True)] == [1]


def test_rank_uses_session_and_context_without_excluding_unknown_metadata():
    tracks = [track(1), track(2), track(3, genre="Ambient Piano")]
    assert WaveEngine._rank_tracks(tracks, positive_artists=["Artist 2"])[0]["id"] == 2
    ranked = WaveEngine._rank_tracks(tracks, mood="calm", activity="work")
    assert ranked[0]["id"] == 3
    assert len(ranked) == 3


@pytest.mark.parametrize("remote_count", [0, 5, 17, 20])
def test_interleave_preserves_quota_and_spreads_artists(remote_count):
    tracks = [track(i, artist=f"Artist {i // 2}") for i in range(20)]
    result = WaveEngine._interleave_wave(tracks[:remote_count], tracks[remote_count:], 20)
    assert {t["id"] for t in result} == set(range(20))
    assert all(a["artist"] != b["artist"] for a, b in zip(result, result[1:]))


def test_search_validates_title_and_all_artists():
    engine = WaveEngine.__new__(WaveEngine)
    engine.ytmusic = SimpleNamespace(search=Mock(return_value=[
        {"videoId": "wrong-title", "title": "Different song", "artists": [{"name": "Target"}]},
        {"videoId": "missing-artist", "title": "Wanted", "artists": []},
        {"videoId": "right", "title": "Wanted (Official Audio)", "artists": [{"name": "Guest"}, {"name": "Target"}]},
    ]))
    assert engine._search_youtube_stream("Target", "Wanted")["external_id"] == "right"


def test_context_search_falls_back_when_artist_does_not_match():
    engine = WaveEngine.__new__(WaveEngine)

    def search(query, filter, limit):
        if filter == "artists":
            return []
        artist = "Target" if query == "Target" else "Unrelated"
        return [{"videoId": artist, "title": "Song", "artists": [{"name": artist}]}]

    engine.ytmusic = SimpleNamespace(search=Mock(side_effect=search))
    result = engine._discover_from_ytmusic_taste(["Target"], set(), set(), set(), mood="calm")
    assert len(result) == 1
    assert result[0]["artist"] == "Target"
    assert result[0]["recommendation_reason"] == "Ещё от Target"
    song_queries = [c.kwargs if c.kwargs else c.args for c in engine.ytmusic.search.call_args_list]
    assert len(song_queries) == 3  # artist lookup, contextual search, plain artist fallback


def test_offline_wave_never_queries_remote_sources(temp_db, tmp_path):
    audio = tmp_path / "song.mp3"
    audio.touch()
    insert_track(1, source="local", path=str(audio))
    insert_track(2, liked=1)
    engine = WaveEngine.__new__(WaveEngine)
    engine.generate_candidates = AsyncMock(side_effect=AssertionError("network used in offline mode"))
    result = asyncio.run(engine.generate_wave(offline=True))
    assert [t["id"] for t in result] == [1]
    engine.generate_candidates.assert_not_called()


def test_all_candidate_sources_obey_same_filters(temp_db, monkeypatch):
    engine = WaveEngine.__new__(WaveEngine)
    engine._resolve_seed_tracks = Mock(return_value=[track(100)])
    engine._resolve_video_id_for_seed = Mock(return_value="seed")
    engine.ytmusic = SimpleNamespace(get_watch_playlist=Mock(return_value={"tracks": []}))
    engine._discover_from_ytmusic_taste = Mock(return_value=[])
    candidates = [track(1, artist="Blocked"), track(2, title="Karaoke"), track(3, duration=900),
                  track(4), track(5, artist="Artist 4", title="Song 4"), track(6)]
    engine.lastfm = SimpleNamespace(get_similar_tracks=AsyncMock(return_value=candidates))
    by_title = {t["title"]: t for t in candidates}
    engine._search_youtube_stream = lambda artist, title: by_title[title]
    monkeypatch.setattr(wave_engine, "get_recently_skipped_artists", lambda: {"blocked"})
    monkeypatch.setattr(wave_engine, "get_wave_exclusions", lambda: {"video-6"})
    result = asyncio.run(engine.generate_candidates([100]))
    assert len(result) == 1
    assert result[0]["title"] == "Song 4"
    assert result[0]["recommendation_reason"] == "Похоже на Artist 100"


@pytest.mark.parametrize("character,limit,expected_local", [("discovery", 1, 0), ("discovery", 20, 1), ("favorite", 20, 15), ("all", 20, 3)])
def test_wave_respects_character_quotas(monkeypatch, character, limit, expected_local):
    engine = WaveEngine.__new__(WaveEngine)
    locals_ = [track(i, source="local") for i in range(1, 21)]
    engine.generate_candidates = AsyncMock(return_value=[track(i) for i in range(100, 140)])
    monkeypatch.setattr(wave_engine, "sample_taste_tracks", lambda *args: locals_)
    monkeypatch.setattr(wave_engine, "get_realtime_session_signals", lambda *args: {})
    result = asyncio.run(engine.generate_wave(limit=limit, character=character))
    assert len(result) == limit
    assert sum(t["source"] == "local" for t in result) == expected_local


def test_wave_backfills_small_library_with_discoveries(monkeypatch):
    engine = WaveEngine.__new__(WaveEngine)
    engine.generate_candidates = AsyncMock(return_value=[track(i) for i in range(100, 140)])
    monkeypatch.setattr(wave_engine, "sample_taste_tracks", lambda *args: [track(1, source="local")])
    monkeypatch.setattr(wave_engine, "get_realtime_session_signals", lambda *args: {})
    result = asyncio.run(engine.generate_wave(limit=20, character="favorite"))
    assert len(result) == 20
    assert len({t["id"] for t in result}) == 20

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
import db
import main
import music_metadata
from lastfm_service import LastFMService
from schemas import HistoryCreate
from wave_feedback import record_event, signal_weight
from wave_engine import WaveEngine

client = TestClient(main.app)


@pytest.fixture
def tracks(tmp_path):
    result = []
    conn = db.get_db()
    try:
        for i in range(1, 9):
            path = tmp_path / f'{i}.mp3'
            path.touch()
            track = dict(id=i, path=str(path), artist=f'Artist {i}', title=f'Song {i}',
                         duration=200, source='local', external_id=f'video-{i}', liked=0)
            conn.execute(f"INSERT INTO tracks ({','.join(track)}) VALUES ({','.join('?' for _ in track)})", tuple(track.values()))
            result.append(track)
        conn.commit()
    finally:
        conn.close()
    return result


def history(track_id=1, event='play', playback_id='play-1', **kwargs):
    payload = dict(track_id=track_id, event=event, playback_id=playback_id, context='wave',
                   duration_seconds=200, recommendation_version='wave-v3', **kwargs)
    response = client.post('/history', json=payload)
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize('event,elapsed,duration,expected', [
    ('play', 0, 200, 0), ('skip', 20, 200, -3), ('skip', 70, 200, -1),
    ('skip', 140, 200, 0), ('skip', 180, 200, 2.5), ('finish', 10, 200, 0),
    ('finish', 180, 200, 2.5), ('error', 180, 200, 0), ('dislike', 180, 200, -4),
    ('stop', 180, 200, 2.5), ('skip', 20, None, -1),
])
def test_feedback_strength(event, elapsed, duration, expected):
    assert signal_weight(event, elapsed, duration) == expected


@pytest.mark.parametrize('field,value', [('elapsed_seconds', -1), ('duration_seconds', 0), ('duration_seconds', -2)])
def test_feedback_validation(tracks, field, value):
    response = client.post('/history', json={'track_id': 1, 'event': 'skip', field: value})
    assert response.status_code == 422


def test_terminal_is_idempotent_and_id_cannot_move_to_other_song(tracks):
    history()
    history(event='skip', elapsed_seconds=30)
    history(event='skip', elapsed_seconds=30)
    history(event='error')
    response = client.post('/history', json={'track_id': 2, 'event': 'play', 'playback_id': 'play-1'})
    assert response.status_code == 409
    conn = db.get_db()
    try:
        rows = conn.execute('SELECT event,listen_ratio,signal_weight FROM history ORDER BY id').fetchall()
        assert [r['event'] for r in rows] == ['play', 'skip']
        assert rows[1]['listen_ratio'] == 0.15
        assert rows[1]['signal_weight'] == -3
    finally:
        conn.close()


def test_near_complete_skip_is_positive_and_errors_do_not_blacklist_artist(tracks):
    history(event='skip', elapsed_seconds=180)
    for i in range(3):
        history(track_id=2, event='error', playback_id=f'error-{i}')
    assert db.get_recently_skipped_artists() == set()
    assert db.get_realtime_session_signals()['positive_track_ids'] == [1]
    assert db.get_realtime_session_signals()['skip_streak'] == 0


def test_metrics_use_distinct_started_playbacks_and_separate_errors(tracks):
    history(is_discovery=True)
    history(event='finish', elapsed_seconds=180, is_discovery=True)
    assert client.post('/tracks/1/like?liked=true&playback_id=play-1').status_code == 200
    assert client.post('/tracks/1/like?liked=true&playback_id=play-1').status_code == 200
    history(playback_id='play-2')
    history(event='skip', playback_id='play-2', elapsed_seconds=30)
    history(track_id=2, playback_id='play-3')
    history(track_id=2, event='error', playback_id='play-3')
    history(track_id=3, event='finish', playback_id='orphan', elapsed_seconds=200)
    data = client.get('/wave/metrics').json()
    stats = data['overall']
    assert stats['plays'] == 3
    assert stats['completion_rate'] == 0.5
    assert stats['early_skip_rate'] == 0.5
    assert stats['error_rate'] == 0.3333
    assert stats['discovery_like_rate'] == 1
    assert stats['adjacent_artist_repeat_rate'] == 0.5
    assert data['by_version']['wave-v3'] == stats


def test_empty_metrics_have_no_fabricated_rates():
    stats = client.get('/wave/metrics').json()['overall']
    assert stats['plays'] == 0
    assert stats['completion_rate'] is None


def test_migration_preserves_history_and_is_repeatable(tracks):
    history()
    db.init_db()
    db.init_db()
    conn = db.get_db()
    try:
        assert conn.execute('SELECT count(*) FROM history').fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize('exclusion', [{'id': 1}, {'external_id': 'video-1'}, {'artist': '  ARTIST   1 ', 'title': 'SONG 1'}])
def test_offline_exclusions_are_hard_even_when_pool_exhausted(tracks, exclusion):
    engine = WaveEngine.__new__(WaveEngine)
    excluded = [exclusion] + [{'id': i} for i in range(2, 9)]
    assert asyncio.run(engine.generate_wave(offline=True, exclude_tracks=excluded)) == []


def test_post_wave_passes_structured_exclusions(tracks, monkeypatch):
    engine = SimpleNamespace(generate_wave=AsyncMock(return_value=[]))
    monkeypatch.setattr(main, 'wave_engine', engine)
    payload = {'limit': 12, 'exclude_tracks': [{'artist': 'Artist 1', 'title': 'Song 1'}]}
    response = client.post('/wave', json=payload)
    assert response.status_code == 200
    assert engine.generate_wave.call_args.kwargs['exclude_tracks'][0]['title'] == 'Song 1'
    assert client.post('/wave', json={'limit': 0}).status_code == 422
    assert client.post('/wave', json={'exclude_tracks': [{}] * 2001}).status_code == 422


def test_exclusions_retry_other_seeds_and_preserve_first_batch_on_retry_error(tracks, monkeypatch):
    engine = WaveEngine.__new__(WaveEngine)
    fresh = {**tracks[0], 'id': -1, 'external_id': 'fresh', 'artist': 'Fresh', 'title': 'New', 'source': 'remote'}
    engine.generate_candidates = AsyncMock(side_effect=[[tracks[0], fresh], RuntimeError('provider down')])
    monkeypatch.setattr('wave_engine.sample_taste_tracks', lambda *args: tracks[1:])
    result = asyncio.run(engine.generate_wave(limit=5, exclude_tracks=[tracks[0]]))
    assert engine.generate_candidates.await_count == 2
    assert fresh['external_id'] in {t['external_id'] for t in result}
    assert not music_metadata.Exclusions([tracks[0]]).contains(result[0])
    assert all(t['external_id'] != 'video-1' for t in result)


def test_tags_are_persisted_and_reused_without_network(tracks):
    service = SimpleNamespace(api_key='test', get_top_tags=Mock(side_effect=lambda artist, title: ['english language', 'ambient'] if title else ['russian rock']))
    enriched = asyncio.run(music_metadata.enrich_tracks(tracks[:1], service))
    assert service.get_top_tags.call_count == 2
    assert enriched[0]['detected_language'] == 'foreign'
    assert 'ambient' in enriched[0]['tags']
    again = asyncio.run(music_metadata.enrich_tracks(tracks[:1], service))
    assert again == enriched
    assert service.get_top_tags.call_count == 2
    assert db._matches_language({**enriched[0], 'title': 'Песня'}, 'foreign')
    assert not db._matches_language({**enriched[0], 'title': 'Песня'}, 'ru')


def test_artist_country_is_not_song_language():
    track = music_metadata.apply_tags({'artist': 'Band', 'title': 'Song'}, [], ['russian', 'russian language'])
    assert track['detected_language'] is None


def test_tag_expiry_and_transport_failure_backoff(tracks):
    conn = db.get_db()
    try:
        conn.execute('INSERT INTO music_tag_cache VALUES (?,?,?,?)', ('artist 1', 'song 1', '["stale"]', time.time()-1))
        conn.commit()
    finally:
        conn.close()
    service = SimpleNamespace(api_key='test', get_top_tags=Mock(return_value=None))
    result = asyncio.run(music_metadata.enrich_tracks(tracks[:1], service))
    assert result[0]['tags'] == []
    asyncio.run(music_metadata.enrich_tracks(tracks[:1], service))
    assert service.get_top_tags.call_count == 2
    conn = db.get_db()
    try:
        assert 0 < conn.execute('SELECT expires_at FROM music_tag_cache LIMIT 1').fetchone()[0] - time.time() <= 300
    finally:
        conn.close()


def test_offline_metadata_does_not_fetch(tracks):
    service = SimpleNamespace(api_key='test', get_top_tags=Mock(side_effect=AssertionError('network')))
    asyncio.run(music_metadata.enrich_tracks(tracks, service, offline=True))
    service.get_top_tags.assert_not_called()


def test_tag_fetch_budget_is_bounded(tracks):
    many = [dict(artist=f'Artist {i}', title=f'Song {i}') for i in range(50)]
    service = SimpleNamespace(api_key='test', get_top_tags=Mock(return_value=['ambient']))
    assert len(asyncio.run(music_metadata.enrich_tracks(many, service))) == 50
    assert service.get_top_tags.call_count == 16


def test_lastfm_tags_normalize_and_use_track_method():
    service = LastFMService(api_key='test')
    service._sync_request = Mock(return_value={'toptags': {'tag': [{'name': ' Ambient '}, {'name': 'ambient'}]}})
    assert service.get_top_tags('Band', 'Song') == ['ambient']
    assert service._sync_request.call_args.args[0]['method'] == 'track.getTopTags'


def test_tags_influence_ranking():
    tracks = [{'id': 1, 'artist': 'A', 'title': 'Song'}, {'id': 2, 'artist': 'B', 'title': 'Song', 'tags': ['ambient']}]
    assert WaveEngine._rank_tracks(tracks, mood='calm')[0]['id'] == 2

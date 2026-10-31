import test from 'node:test';
import assert from 'node:assert/strict';
import { ListeningTracker, uniqueTracks, WaveRequests, WaveRevision, playbackId, requestWave } from '../static/js/wave_feedback.js';
import PlaybackQueue from '../static/js/playback_queue.js';
const song = (id, artist = `Artist ${id}`, title = `Song ${id}`) => ({ id, artist, title, external_id: `video-${id}` });
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('wave API mismatch and invalid requests do not retry the same endpoint offline', async () => {
    for (const status of [404, 405, 422, 429]) {
        let calls = 0;
        await assert.rejects(requestWave(async () => {
            calls++;
            throw Object.assign(new Error(`HTTP ${status}`), { status });
        }, { offline: false }), error => error.status === status &&
            (![404, 405].includes(status) || error.message.includes('Перезапустите сервер')));
        assert.equal(calls, 1);
    }
});

test('temporary wave failure retries offline once and keeps exclusions', async () => {
    for (const error of [new TypeError('Failed to fetch'), Object.assign(new Error('HTTP 503'), { status: 503 })]) {
        const calls = [];
        const payload = { offline: false, limit: 12, exclude_tracks: [song(1)] };
        const result = await requestWave(async (url, options) => {
            assert.equal(url, '/wave');
            assert.equal(options.method, 'POST');
            calls.push(JSON.parse(options.body));
            if (calls.length === 1) throw error;
            return [song(2)];
        }, payload);
        assert.deepEqual(result, [song(2)]);
        assert.deepEqual(calls, [payload, { ...payload, offline: true }]);
        assert.equal(payload.offline, false);
    }
});

test('offline wave failure stops after one request', async () => {
    let calls = 0;
    await assert.rejects(requestWave(async () => {
        calls++;
        throw new TypeError('Failed to fetch');
    }, { offline: true }));
    assert.equal(calls, 1);
});

test('cancelling a slow wave does not start an offline retry', async () => {
    const controller = new AbortController();
    let calls = 0;
    const result = requestWave(async (url, { signal }) => {
        calls++;
        assert.equal(signal, controller.signal);
        return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    }, { offline: false }, { signal: controller.signal });
    controller.abort();
    await assert.rejects(result, { name: 'AbortError' });
    assert.equal(calls, 1);
});

// IDs may change when remote songs are saved or re-uploaded.
test('deduplicate across batches and inside a response by every identity', () => {
    const queue = new PlaybackQueue();
    queue.setQueue([song(1)]);
    queue.appendTracks([song(2, ' ARTIST  1 ', 'song 1'), song(3), song(3), song(4)]);
    assert.deepEqual(queue.queue.map(t => t.id), [1, 3, 4]);
    assert.deepEqual(uniqueTracks([song(1)], queue.queue), []);
});

test('refresh preserves playing object, index and listened prefix', () => {
    const queue = new PlaybackQueue();
    queue.setQueue([song(1), song(2), song(3)]);
    queue.playAt(1);
    const current = queue.getCurrentTrack();
    queue.replaceUpcoming([song(1), song(4), song(4)]);
    assert.equal(queue.getCurrentTrack(), current);
    assert.equal(queue.index, 1);
    assert.deepEqual(queue.queue.map(t => t.id), [1, 2, 4]);
    queue.replaceUpcoming([]);
    assert.deepEqual(queue.queue.map(t => t.id), [1, 2]);
});

test('listening excludes forward/backward seeks and replayed intervals', () => {
    const tracker = new ListeningTracker();
    tracker.sample(0, 0, true);
    tracker.sample(10, 10000, true);
    tracker.sample(150, 11000, false); // seeking
    tracker.sample(150, 11000, true);
    tracker.sample(155, 16000, true);
    tracker.sample(0, 17000, false);
    tracker.sample(0, 17000, true);
    tracker.sample(10, 27000, true); // already counted
    assert.equal(tracker.seconds, 15);
});

test('listening excludes pause, buffering and unexplained position jumps', () => {
    const tracker = new ListeningTracker();
    tracker.sample(0, 0, true);
    tracker.sample(5, 5000, true);
    tracker.sample(5, 5000, false);
    tracker.sample(5, 30000, false);
    tracker.sample(5, 30000, true);
    tracker.sample(10, 35000, true);
    tracker.sample(190, 36000, true);
    assert.equal(tracker.seconds, 10);
});

test('listening at 2x counts media duration, not wall time', () => {
    const tracker = new ListeningTracker();
    tracker.sample(0, 0, true, 2);
    tracker.sample(20, 10000, true, 2);
    assert.equal(tracker.seconds, 20);
});

test('like supersedes in-flight prefetch and never restarts current song', async () => {
    const queue = new PlaybackQueue(); queue.setQueue([song(1), song(2)]); queue.playAt(0);
    const current = queue.getCurrentTrack();
    const a = deferred(), b = deferred(); let count = 0;
    const requests = new WaveRequests({ queue, revision: new WaveRevision(), mode: () => 'wave',
        fetch: () => (++count === 1 ? a.promise : b.promise) });
    const prefetch = requests.prefetch(); await tick();
    const refresh = requests.refresh(false); await tick();
    b.resolve([song(5)]); await refresh;
    a.resolve([song(9)]); await prefetch;
    assert.deepEqual(queue.queue.map(t => t.id), [1, 5]);
    assert.equal(queue.getCurrentTrack(), current);
});

test('feedback arriving during refresh triggers a new request instead of being dropped', async () => {
    const queue = new PlaybackQueue(); queue.setQueue([song(1)]); queue.playAt(0);
    const a = deferred(), b = deferred(); const calls = [];
    const requests = new WaveRequests({ queue, revision: new WaveRevision(), mode: () => 'wave',
        fetch: (limit, options) => { calls.push(options); return calls.length === 1 ? a.promise : b.promise; } });
    const done = requests.refresh(false); await tick();
    requests.refresh(true);
    a.resolve([song(2)]); await tick();
    assert.deepEqual(queue.queue.map(t => t.id), [1]);
    b.resolve([song(3)]); await done;
    assert.deepEqual(calls, [{ shake: false }, { shake: true }]);
    assert.deepEqual(queue.queue.map(t => t.id), [1, 3]);
});

test('leaving wave discards late responses', async () => {
    const queue = new PlaybackQueue(); queue.setQueue([song(1)]); queue.playAt(0);
    const a = deferred(); let mode = 'wave';
    const revision = new WaveRevision();
    const requests = new WaveRequests({ queue, revision, mode: () => mode, fetch: () => a.promise });
    const done = requests.refresh(); await tick();
    mode = 'list'; revision.invalidate(); queue.setQueue([song(10)]); queue.playAt(0);
    a.resolve([song(2)]); await done;
    assert.deepEqual(queue.queue.map(t => t.id), [10]);
});

test('recommendations wait for persisted feedback', async () => {
    const queue = new PlaybackQueue(); queue.setQueue([song(1)]); queue.playAt(0);
    const write = deferred(); let fetched = false;
    const requests = new WaveRequests({ queue, revision: new WaveRevision(), mode: () => 'wave', before: () => write.promise,
        fetch: async () => { fetched = true; return []; } });
    const done = requests.refresh(); await tick();
    assert.equal(fetched, false);
    write.resolve(); await done;
    assert.equal(fetched, true);
});

test('playback IDs work on insecure LAN origins without randomUUID', () => {
    const old = globalThis.crypto;
    Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true });
    try { assert.notEqual(playbackId(), playbackId()); }
    finally { Object.defineProperty(globalThis, 'crypto', { value: old, configurable: true }); }
});

export const normalize = value => String(value || '').toLowerCase().trim().replace(/\s+/g, ' ');

export async function requestWave(fetchJson, payload, { signal } = {}) {
    const request = body => fetchJson('/wave', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), timeoutMs: 45000, signal
    });
    try {
        return await request(payload);
    } catch (error) {
        if (signal?.aborted) throw error;
        // An offline request uses the same endpoint and cannot repair an API mismatch.
        if (error.status === 404 || error.status === 405) {
            error.message = 'Сервер ещё не обновлён. Перезапустите сервер Écho и обновите страницу.';
            throw error;
        }
        if (payload.offline || (error.status >= 400 && error.status < 500)) throw error;
        return await request({ ...payload, offline: true });
    }
}

export function playbackId() {
    // randomUUID is unavailable on ordinary HTTP LAN origins used by the phone UI.
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

export function identities(track) {
    const keys = [];
    if (Number(track.id) > 0) keys.push(`id:${track.id}`);
    if (track.external_id) keys.push(`external:${track.external_id}`);
    if (track.artist && track.title) keys.push(`song:${JSON.stringify([normalize(track.artist), normalize(track.title)])}`);
    if (!keys.length && track.path) keys.push(`path:${track.path}`);
    return keys;
}

export function uniqueTracks(tracks, excluded = []) {
    const seen = new Set(excluded.flatMap(identities));
    return tracks.filter(track => {
        const keys = identities(track);
        const duplicate = keys.some(key => seen.has(key));
        keys.forEach(key => seen.add(key));
        return keys.length && !duplicate;
    });
}

// Accumulate unique audible media intervals, excluding seeks, buffering and pauses.
export class ListeningTracker {
    constructor() {
        this.ranges = [];
        this.previous = null;
    }

    sample(position, now, active, rate = 1) {
        const previous = this.previous;
        this.previous = { position, now, active };
        if (!previous || !active || !previous.active) return;
        const delta = position - previous.position;
        const wall = Math.max(0, (now - previous.now) / 1000);
        if (delta <= 0 || delta > wall * rate + 0.35) return;
        this.ranges.push([previous.position, position]);
        this.ranges.sort((a, b) => a[0] - b[0]);
        const merged = [];
        for (const range of this.ranges) {
            const last = merged.at(-1);
            if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
            else merged.push([...range]);
        }
        this.ranges = merged;
    }

    get seconds() {
        return this.ranges.reduce((total, [start, end]) => total + end - start, 0);
    }
}

// A result may only update the queue that requested it.
export class WaveRevision {
    constructor() { this.value = 0; }
    invalidate() { return ++this.value; }
    accepts(token, mode) { return mode === 'wave' && token === this.value; }
}

export class WaveRequests {
    constructor({ queue, revision, mode, fetch, before = () => Promise.resolve(), status = () => {} }) {
        Object.assign(this, { queue, revision, mode, fetch, before, status });
        this.refreshPromise = null;
        this.prefetchPromise = null;
        this.pending = false;
        this.shake = false;
    }

    refresh(shake = true) {
        if (this.mode() !== 'wave') return Promise.resolve();
        this.revision.invalidate();
        this.pending = true;
        this.shake ||= shake;
        if (this.refreshPromise) return this.refreshPromise;
        this.refreshPromise = (async () => {
            while (this.pending && this.mode() === 'wave') {
                this.pending = false;
                const shake = this.shake;
                this.shake = false;
                const token = this.revision.value;
                this.status('Обновляем «Далее»…');
                try {
                    await this.before();
                    const tracks = await this.fetch(15, { shake });
                    if (this.revision.accepts(token, this.mode())) this.queue.replaceUpcoming(tracks);
                } catch (error) {
                    console.warn('Не удалось обновить волну:', error);
                }
            }
        })().finally(() => { this.refreshPromise = null; this.status(null); });
        return this.refreshPromise;
    }

    prefetch() {
        if (this.mode() !== 'wave') return Promise.resolve();
        if (this.refreshPromise) return this.refreshPromise;
        if (this.prefetchPromise) return this.prefetchPromise;
        if (this.queue.length - this.queue.index > 3) return Promise.resolve();
        const token = this.revision.value;
        this.status('Подбираю следующие треки…');
        this.prefetchPromise = (async () => {
            try {
                await this.before();
                const tracks = await this.fetch(12);
                if (this.revision.accepts(token, this.mode())) this.queue.appendTracks(tracks);
            } catch (error) {
                console.warn('Не удалось продолжить волну:', error);
            }
        })().finally(() => { this.prefetchPromise = null; this.status(null); });
        return this.prefetchPromise;
    }
}

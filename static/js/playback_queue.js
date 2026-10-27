class PlaybackQueue {
    constructor() {
        this.queue = [];
        this.currentIndex = -1;
    }

    setQueue(tracks) {
        const current = this.getCurrentTrack();
        this.queue = Array.isArray(tracks) ? [...tracks] : [];
        if (current) {
            const newIdx = this.queue.findIndex(t => 
                (t.id && current.id && t.id !== -1 && t.id !== '-1' && t.id === current.id) || 
                (t.title === current.title && t.artist === current.artist)
            );
            this.currentIndex = newIdx !== -1 ? newIdx : 0;
        } else {
            this.currentIndex = -1;
        }
    }

    appendTracks(newTracks) {
        if (!Array.isArray(newTracks) || newTracks.length === 0) return;
        const existing = new Set(this.queue.map(t => (t.external_id || t.path || t.id)));
        const toAdd = newTracks.filter(t => !existing.has(t.external_id || t.path || t.id));
        this.queue.push(...toAdd);
    }

    next() {
        if (this.currentIndex < this.queue.length - 1) {
            this.currentIndex++;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    previous() {
        if (this.currentIndex > 0) {
            this.currentIndex--;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    playAt(index) {
        if (index >= 0 && index < this.queue.length) {
            this.currentIndex = index;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    getCurrentTrack() {
        return this.queue[this.currentIndex] || null;
    }

    get length() {
        return this.queue.length;
    }

    get index() {
        return this.currentIndex;
    }
}

export default PlaybackQueue;

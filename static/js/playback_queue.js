class PlaybackQueue {
    constructor() {
        this.queue = [];
        this.currentIndex = -1;
    }

    /**
     * Устанавливает новый список треков и сбрасывает указатель.
     * @param {Array} tracks - Массив объектов треков.
     */
    setQueue(tracks) {
        this.queue = Array.isArray(tracks) ? [...tracks] : [];
        this.currentIndex = -1;
    }

    /**
     * Возвращает следующий трек и инкрементирует индекс.
     * @returns {Object|null} Следующий трек или null, если конец очереди.
     */
    next() {
        if (this.currentIndex < this.queue.length - 1) {
            this.currentIndex++;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    /**
     * Возвращает предыдущий трек и декрементирует индекс.
     * @returns {Object|null} Предыдущий трек или null, если начало очереди.
     */
    previous() {
        if (this.currentIndex > 0) {
            this.currentIndex--;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    /**
     * Устанавливает трек по индексу.
     * @param {number} index 
     * @returns {Object|null} Трек или null, если индекс вне диапазона.
     */
    playAt(index) {
        if (index >= 0 && index < this.queue.length) {
            this.currentIndex = index;
            return this.queue[this.currentIndex];
        }
        return null;
    }

    /**
     * Возвращает текущий трек.
     * @returns {Object|null}
     */
    getCurrentTrack() {
        return this.queue[this.currentIndex] || null;
    }

    /**
     * Возвращает длину очереди.
     * @returns {number}
     */
    get length() {
        return this.queue.length;
    }

    /**
     * Возвращает текущий индекс.
     * @returns {number}
     */
    get index() {
        return this.currentIndex;
    }
}

export default PlaybackQueue;

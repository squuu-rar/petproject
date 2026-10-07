import PlaybackQueue from './playback_queue.js';

document.addEventListener('DOMContentLoaded', () => {
    const DEFAULT_COVER = '/static/img/default-cover.svg';
    const WAVE_STATUS_IDLE = 'Поток подстраивается под ваши лайки и пропуски';

    const tracklist = document.getElementById('tracklist');
    const viewTitle = document.getElementById('view-title');
    const audioPlayer = document.getElementById('audio-player');

    const playerTitle = document.getElementById('player-title');
    const playerArtist = document.getElementById('player-artist');
    const playerCover = document.getElementById('player-cover');
    const btnPlay = document.getElementById('btn-play');
    const btnNext = document.getElementById('btn-next');
    const btnPrev = document.getElementById('btn-prev');
    const btnLikePlayer = document.getElementById('btn-like-player');
    const progressFill = document.getElementById('progress-fill');
    const progressTrack = document.querySelector('.progress-track');
    const timeCurrent = document.getElementById('time-current');
    const timeTotal = document.getElementById('time-total');
    const btnMute = document.getElementById('btn-mute');
    const volumeRange = document.getElementById('volume-range');

    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let queueMode = 'list';
    let queueListRef = null;
    let visibleTracks = [];
    let isPrefetchingWave = false;
    let autoSkipTimer = null;
    let searchDebounceTimer = null;
    let searchSeq = 0;
    let playToken = 0;
    let viewSeq = 0;
    let consecutiveErrors = 0;
    let waveEls = null;

    const store = {
        get(key, fallback) {
            try {
                const v = localStorage.getItem(key);
                return v === null ? fallback : v;
            } catch { return fallback; }
        },
        set(key, value) {
            try { localStorage.setItem(key, value); } catch {}
        }
    };

    function showToast(msg) {
        let toast = document.getElementById('player-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'player-toast';
            toast.className = 'toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.classList.add('visible');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => toast.classList.remove('visible'), 2600);
    }

    function formatTime(seconds) {
        if (!seconds || !isFinite(seconds)) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function hasValidId(track) {
        return Boolean(track) && Boolean(track.id) && track.id !== -1 && track.id !== '-1';
    }

    function isSameTrack(a, b) {
        if (!a || !b) return false;
        if (a.external_id && b.external_id && a.external_id === b.external_id) return true;
        if (hasValidId(a) && hasValidId(b)) return String(a.id) === String(b.id);
        return a.title === b.title && a.artist === b.artist;
    }

    function trackKey(t) {
        if (!t) return '';
        if (t.external_id) return `x:${t.external_id}`;
        if (hasValidId(t)) return `i:${t.id}`;
        return `t:${t.title}|${t.artist}`;
    }

    function setImgWithFallback(img, src) {
        if (!img) return;
        img.src = src || DEFAULT_COVER;
    }

    async function fetchJson(url, options) {
        const res = await fetch(url, options);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    function fetchTracks(params = {}) {
        return fetchJson(`/tracks?${new URLSearchParams(params).toString()}`);
    }

    function fetchFavorites(params = {}) {
        return fetchJson(`/favorites?${new URLSearchParams({ limit: 100, ...params }).toString()}`);
    }

    function searchTracks(query) {
        return fetchJson(`/search?q=${encodeURIComponent(query)}`);
    }

    async function fetchWave(limit) {
        const data = await fetchJson(`/wave?limit=${limit}`);
        if (!Array.isArray(data)) throw new Error('Волна вернула не список');
        return data;
    }

    function toggleLike(trackId, isLiked) {
        return fetchJson(`/tracks/${trackId}/like?liked=${!isLiked}`, { method: 'POST' });
    }

    function sendHistory(trackId, event, elapsedSeconds = 0) {
        if (!trackId || trackId === -1 || trackId === '-1') return;
        fetch('/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_id: trackId, event, elapsed_seconds: elapsedSeconds })
        }).catch(() => {});
    }

    function setAppVolume(val0to1, unmute = true) {
        const vol = Math.min(1, Math.max(0, val0to1));
        audioPlayer.volume = vol;
        if (unmute && vol > 0) {
            audioPlayer.muted = false;
            store.set('player:muted', '0');
        }
        const percent = Math.round(vol * 100);
        store.set('player:volume', String(percent));
        if (volumeRange) volumeRange.value = String(percent);
        syncVolumeUI();
    }

    function syncVolumeUI() {
        const muted = audioPlayer.muted || audioPlayer.volume === 0;
        const cls = muted ? 'fas fa-volume-xmark'
            : (audioPlayer.volume < 0.4 ? 'fas fa-volume-low' : 'fas fa-volume-high');
        if (btnMute) {
            const icon = btnMute.querySelector('i');
            if (icon) icon.className = cls;
        }
        if (waveEls && waveEls.mute) {
            const icon = waveEls.mute.querySelector('i');
            if (icon) icon.className = cls;
        }

        const percent = Math.round(audioPlayer.volume * 100);
        if (waveEls && waveEls.volumeSlider) waveEls.volumeSlider.value = String(percent);
        if (waveEls && waveEls.volumeVal) {
            waveEls.volumeVal.textContent = muted ? 'Выкл' : `${percent}%`;
        }
    }

    function toggleMute() {
        audioPlayer.muted = !audioPlayer.muted;
        store.set('player:muted', audioPlayer.muted ? '1' : '0');
        syncVolumeUI();
    }

    (function restoreVolume() {
        const saved = Number(store.get('player:volume', '70'));
        const percent = Math.min(100, Math.max(0, isFinite(saved) ? saved : 70));
        if (volumeRange) volumeRange.value = String(percent);
        audioPlayer.volume = percent / 100;
        audioPlayer.muted = store.get('player:muted', '0') === '1';
        syncVolumeUI();
    })();

    function bindSeek(trackEl) {
        if (!trackEl) return;
        const seekTo = (clientX) => {
            if (!audioPlayer.duration || !isFinite(audioPlayer.duration)) return;
            const rect = trackEl.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            audioPlayer.currentTime = pos * audioPlayer.duration;
        };
        trackEl.addEventListener('pointerdown', (e) => {
            if (!audioPlayer.duration || !isFinite(audioPlayer.duration)) return;
            seekTo(e.clientX);
            const move = (ev) => seekTo(ev.clientX);
            const up = () => {
                window.removeEventListener('pointermove', move);
                window.removeEventListener('pointercancel', up);
            };
            window.addEventListener('pointermove', move);
            window.addEventListener('pointerup', up, { once: true });
            window.addEventListener('pointercancel', up, { once: true });
        });
    }

    function setHeartIcon(btn, liked) {
        if (!btn) return;
        btn.classList.toggle('liked', Boolean(liked));
        const icon = btn.querySelector('i');
        if (icon) icon.className = liked ? 'fas fa-heart' : 'far fa-heart';
    }

    function syncPlayPauseUI(isPlaying) {
        if (btnPlay) {
            btnPlay.querySelector('i').className = isPlaying ? 'fas fa-pause-circle' : 'fas fa-play-circle';
        }
        if (waveEls && waveEls.play) {
            const icon = waveEls.play.querySelector('i');
            icon.className = isPlaying ? 'fas fa-pause' : 'fas fa-play';
            icon.style.marginLeft = isPlaying ? '0' : '4px';
        }
        updateActiveTrackUI();
    }

    function syncLikeUI() {
        setHeartIcon(btnLikePlayer, currentlyPlayingTrack && currentlyPlayingTrack.liked);
        if (waveEls && waveEls.like && currentlyPlayingTrack) setHeartIcon(waveEls.like, currentlyPlayingTrack.liked);
        tracklist.querySelectorAll('.track-row').forEach(row => {
            if (row._track) setHeartIcon(row.querySelector('.track-like-btn'), row._track.liked);
        });
    }

    async function applyLike(track) {
        if (!track) return;
        try {
            const wasLiked = Boolean(track.liked);
            await toggleLike(track.id, wasLiked);
            track.liked = !wasLiked;
            if (currentlyPlayingTrack && isSameTrack(currentlyPlayingTrack, track)) {
                currentlyPlayingTrack.liked = track.liked;
            }
            syncLikeUI();
        } catch (err) {
            showToast('⚠️ Ошибка изменения статуса');
        }
    }

    function updateActiveTrackUI() {
        const playing = !audioPlayer.paused;
        tracklist.querySelectorAll('.track-row').forEach(row => {
            const active = Boolean(currentlyPlayingTrack) && isSameTrack(row._track, currentlyPlayingTrack);
            row.classList.toggle('active', active);
            row.classList.toggle('playing', active && playing);
        });
    }

    function renderWaveIdle({ loading = false } = {}) {
        waveEls = null;
        tracklist.innerHTML = `
            <div class="wave-screen">
                <div class="wave-glow"></div>
                <div class="wave-stage">
                    <span class="wave-label">Бесконечный поток</span>
                    <h2 class="wave-idle-title">Моя волна</h2>
                    <button id="btn-wave-start" class="wave-btn-circle">
                        ${loading ? '<i class="fas fa-spinner fa-spin"></i>' : '<i class="fas fa-play" style="margin-left:4px;"></i>'}
                    </button>
                    <p class="wave-idle-note">${loading ? 'Изучаю звучание…' : 'Нажмите для старта волны'}</p>
                </div>
            </div>
        `;
        document.getElementById('btn-wave-start')?.addEventListener('click', initWavePlayback);
    }

    function mountWaveScreen() {
        tracklist.innerHTML = `
            <div class="wave-screen">
                <div class="wave-backdrop" id="wave-backdrop"></div>
                <div class="wave-glow"></div>
                <div class="wave-stage" id="wave-stage">
                    <span class="wave-label">Моя волна</span>
                    <h2 class="wave-artist" id="wave-artist"></h2>
                    <button class="wave-cover-btn" id="wave-cover-btn">
                        <img id="wave-cover" src="${DEFAULT_COVER}" alt="Обложка">
                    </button>

                    <div class="wave-actions">
                        <div class="wave-actions-side wave-actions-left">
                            <div class="wave-volume-wrap" id="wave-volume-wrap">
                                <button id="wave-mute-btn" class="wave-round" title="Звук"><i class="fas fa-volume-high"></i></button>
                                <div class="wave-volume-popover">
                                    <input type="range" id="wave-volume-slider" class="wave-volume-slider" min="0" max="100" value="70">
                                    <span class="wave-volume-val" id="wave-volume-val">70%</span>
                                </div>
                            </div>
                            <button id="wave-dislike-btn" class="wave-round" title="Не нравится"><i class="fas fa-ban"></i></button>
                        </div>

                        <div class="wave-capsule"><span id="wave-title"></span></div>

                        <div class="wave-actions-side wave-actions-right">
                            <button id="wave-like-btn" class="wave-round" title="Нравится"><i class="far fa-heart"></i></button>
                            <button id="wave-copy-btn" class="wave-round" title="Скопировать название"><i class="fas fa-arrow-up-from-bracket"></i></button>
                        </div>
                    </div>

                    <div class="wave-progress">
                        <span class="progress-time" id="wave-time-current">0:00</span>
                        <div class="progress-track" id="wave-progress-track"><div class="progress-fill" id="wave-progress-fill"></div></div>
                        <span class="progress-time" id="wave-time-total">0:00</span>
                    </div>

                    <div class="wave-transport">
                        <button id="wave-prev-btn" class="wave-btn-side"><i class="fas fa-backward-step"></i></button>
                        <button id="wave-play-toggle" class="wave-btn-circle"><i class="fas fa-play" style="margin-left:4px;"></i></button>
                        <button id="wave-next-btn" class="wave-btn-side"><i class="fas fa-forward-step"></i></button>
                    </div>

                    <div class="wave-status"><i class="fas fa-wand-magic-sparkles"></i><span id="wave-status-text">${WAVE_STATUS_IDLE}</span></div>
                    <button id="wave-shake-btn" class="wave-shake">Встряхнуть волну</button>
                </div>
            </div>
        `;

        const $ = (id) => document.getElementById(id);
        waveEls = {
            screen: tracklist.querySelector('.wave-screen'),
            backdrop: $('wave-backdrop'),
            stage: $('wave-stage'),
            artist: $('wave-artist'),
            title: $('wave-title'),
            cover: $('wave-cover'),
            like: $('wave-like-btn'),
            mute: $('wave-mute-btn'),
            volumeSlider: $('wave-volume-slider'),
            volumeVal: $('wave-volume-val'),
            play: $('wave-play-toggle'),
            timeCur: $('wave-time-current'),
            timeTotal: $('wave-time-total'),
            progressTrack: $('wave-progress-track'),
            progressFill: $('wave-progress-fill'),
            status: $('wave-status-text'),
            lastKey: null
        };

        bindSeek(waveEls.progressTrack);
        $('wave-cover-btn').addEventListener('click', togglePlay);
        waveEls.play.addEventListener('click', togglePlay);
        $('wave-next-btn').addEventListener('click', () => nextTrack(true));$('wave-prev-btn').addEventListener('click', prevTrack);
        waveEls.mute.addEventListener('click', toggleMute);

        if (waveEls.volumeSlider) {
            waveEls.volumeSlider.addEventListener('input', (e) => {
                setAppVolume(parseFloat(e.target.value) / 100);
            });
        }
        const volWrap = $('wave-volume-wrap');
        if (volWrap) {
            volWrap.addEventListener('wheel', (e) => {
                e.preventDefault();
                setAppVolume(audioPlayer.volume + (e.deltaY < 0 ? 0.05 : -0.05));
            }, { passive: false });
        }

        waveEls.like.addEventListener('click', () => applyLike(currentlyPlayingTrack));
        $('wave-dislike-btn').addEventListener('click', () => nextTrack(false));$('wave-shake-btn').addEventListener('click', initWavePlayback);

        $('wave-copy-btn')?.addEventListener('click', () => {
            if (!currentlyPlayingTrack) return;
            const text = `${currentlyPlayingTrack.artist} — ${currentlyPlayingTrack.title}`;
            navigator.clipboard.writeText(text).then(() => showToast(`📋 Скопировано: ${text}`));
        });

        syncVolumeUI();
    }

    function updateWaveScreen() {
        const t = currentlyPlayingTrack;
        if (!t || !waveEls) return;

        const key = trackKey(t);
        const changed = waveEls.lastKey !== key;
        waveEls.lastKey = key;

        waveEls.artist.textContent = t.artist || 'Неизвестный исполнитель';
        waveEls.title.textContent = t.title || 'Без названия';

        if (changed) {
            setImgWithFallback(waveEls.cover, t.cover_path);
            waveEls.backdrop.style.backgroundImage = t.cover_path ? `url("${encodeURI(t.cover_path)}")` : 'none';
            waveEls.progressFill.style.width = '0%';
            waveEls.timeCur.textContent = '0:00';
            waveEls.timeTotal.textContent = formatTime(t.duration);
        }

        setHeartIcon(waveEls.like, t.liked);
        syncPlayPauseUI(!audioPlayer.paused);
    }

    function renderWaveView() {
        if (!tracklist) return;
        if (currentlyPlayingTrack) {
            mountWaveScreen();
            updateWaveScreen();
        } else {
            renderWaveIdle();
        }
    }

    async function initWavePlayback() {
        if (currentView === 'wave') renderWaveIdle({ loading: true });
        try {
            const tracks = await fetchWave(20);
            if (tracks.length === 0) {
                showToast('Волна пока пуста');
                if (currentView === 'wave') renderWaveIdle();
                return;
            }
            queueMode = 'wave';
            queueListRef = null;
            playbackQueue.setQueue(tracks);
            await playTrack(0, { force: true });
            if (currentView === 'wave') renderWaveView();
        } catch (e) {
            showToast('⚠️️ Ошибка запуска волны');
            if (currentView === 'wave' && !currentlyPlayingTrack) renderWaveIdle();
        }
    }

    async function prefetchWaveIfNeeded() {
        if (queueMode !== 'wave' || isPrefetchingWave) return;
        if (playbackQueue.length - playbackQueue.index > 3) return;
        isPrefetchingWave = true;
        try {
            const extra = await fetchWave(12);
            playbackQueue.appendTracks(extra);
        } catch {} finally {
            isPrefetchingWave = false;
        }
    }

    function renderSearchView() {
        tracklist.innerHTML = `
            <div class="search-view">
                <input type="search" id="search-input" class="search-input-field" placeholder="Поиск музыки..." autofocus>
                <div id="search-results-zone"></div>
            </div>
        `;
        const input = document.getElementById('search-input');
        input?.addEventListener('input', (e) => {
            clearTimeout(searchDebounceTimer);
            const q = e.target.value.trim();
            if (!q) {
                document.getElementById('search-results-zone').innerHTML = '';
                return;
            }
            searchDebounceTimer = setTimeout(async () => {
                const zone = document.getElementById('search-results-zone');
                zone.innerHTML = '<div style="padding:20px;color:#888;">Поиск...</div>';
                try {
                    const res = await searchTracks(q);
                    visibleTracks = res;
                    playbackQueue.setQueue(res);
                    zone.innerHTML = '';
                    const wrap = document.createElement('div');
                    wrap.className = 'track-list-wrapper';
                    res.forEach((t, i) => wrap.appendChild(createTrackRow(t, res, i)));
                    zone.appendChild(wrap);
                } catch {
                    zone.innerHTML = '<div style="padding:20px;color:red;">Ошибка поиска</div>';
                }
            }, 300);
        });
    }

    function createTrackRow(track, list, index) {
        const row = document.createElement('div');
        row.className = 'track-row';
        row._track = track;
        row.innerHTML = `
            <span class="track-row-index">${index + 1}</span>
            <div class="track-row-cover-wrap">
                <img src="${escapeHtml(track.cover_path || DEFAULT_COVER)}" alt="" class="track-row-cover">
            </div>
            <div class="track-row-titles">
                <span class="track-row-title">${escapeHtml(track.title || 'Без названия')}</span>
                <span class="track-row-artist">${escapeHtml(track.artist || 'Неизвестный исполнитель')}</span>
            </div>
            <span class="track-row-album">${escapeHtml(track.album || '-')}</span>
            <span class="track-row-duration">${formatTime(track.duration)}</span>
            <button class="track-like-btn ${track.liked ? 'liked' : ''}"><i class="${track.liked ? 'fas' : 'far'} fa-heart"></i></button>
        `;
        row.addEventListener('click', (e) => {
            if (e.target.closest('.track-like-btn')) return;
            queueMode = 'list';
            queueListRef = list;
            playbackQueue.setQueue(list);
            playTrack(index);
        });
        return row;
    }

    function renderTracklist(tracks) {
        visibleTracks = Array.isArray(tracks) ? tracks : [];
        tracklist.innerHTML = '';
        if (visibleTracks.length === 0) {
            tracklist.innerHTML = '<div style="padding:36px;color:#888;">Треки не найдены</div>';
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'track-list-wrapper';
        visibleTracks.forEach((t, i) => wrapper.appendChild(createTrackRow(t, visibleTracks, i)));
        tracklist.appendChild(wrapper);
    }

    async function playTrack(index, { force = false } = {}) {
        if (index < 0 || index >= playbackQueue.length) return;
        clearTimeout(autoSkipTimer);
        const track = playbackQueue.queue[index];

        if (!force && isSameTrack(currentlyPlayingTrack, track) && audioPlayer.src && !audioPlayer.error) {
            if (audioPlayer.paused) audioPlayer.play().catch(() => {});
            else audioPlayer.pause();
            return;
        }

        playbackQueue.playAt(index);
        currentlyPlayingTrack = track;

        playerTitle.textContent = track.title || 'Без названия';
        playerArtist.textContent = track.artist || 'Неизвестный исполнитель';
        setImgWithFallback(playerCover, track.cover_path);
        setHeartIcon(btnLikePlayer, track.liked);
        if (progressFill) progressFill.style.width = '0%';
        if (timeCurrent) timeCurrent.textContent = '0:00';
        if (timeTotal) timeTotal.textContent = formatTime(track.duration);

        if (currentView === 'wave') renderWaveView();
        updateActiveTrackUI();

        if (!hasValidId(track)) {
            try {
                const saved = await fetchJson('/tracks/remote', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(track)
                });
                track.id = saved.id;
            } catch {
                autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                return;
            }
        }

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(() => syncPlayPauseUI(false));
        sendHistory(track.id, 'play', 0);
        prefetchWaveIfNeeded();
    }

    function togglePlay() {
        if (!currentlyPlayingTrack) {
            if (visibleTracks.length > 0) playTrack(0);
            else if (playbackQueue.length > 0) playTrack(0);
            return;
        }
        if (audioPlayer.paused) {
            audioPlayer.play().catch(() => syncPlayPauseUI(false));
        } else {
            audioPlayer.pause();
        }
    }

    function nextTrack(isSkip = false) {
        clearTimeout(autoSkipTimer);
        if (isSkip && currentlyPlayingTrack) {
            sendHistory(currentlyPlayingTrack.id, 'skip', audioPlayer.currentTime || 0);
        }
        if (playbackQueue.next()) {
            playTrack(playbackQueue.index);
        } else if (queueMode === 'wave') {
            prefetchWaveIfNeeded().then(() => {
                if (playbackQueue.next()) playTrack(playbackQueue.index);
            });
        }
    }

    function prevTrack() {
        clearTimeout(autoSkipTimer);
        if (playbackQueue.previous()) playTrack(playbackQueue.index);
    }

    audioPlayer.addEventListener('play', () => syncPlayPauseUI(true));
    audioPlayer.addEventListener('pause', () => syncPlayPauseUI(false));
    audioPlayer.addEventListener('error', () => {
        syncPlayPauseUI(false);
        consecutiveErrors += 1;
        if (consecutiveErrors > 4) {
            consecutiveErrors = 0;
            audioPlayer.removeAttribute('src');
            audioPlayer.load();
            showToast("⚠️ Ошибка воспроизведения");
            return;
        }
        autoSkipTimer = setTimeout(() => nextTrack(false), 1800);
    });

    audioPlayer.addEventListener('timeupdate', () => {
        const cur = audioPlayer.currentTime;
        const dur = audioPlayer.duration || 0;
        const pct = dur > 0 ? `${(cur / dur) * 100}%` : '0%';
        if (timeCurrent) timeCurrent.textContent = formatTime(cur);
        if (progressFill) progressFill.style.width = pct;
        if (waveEls && waveEls.timeCur) waveEls.timeCur.textContent = formatTime(cur);
        if (waveEls && waveEls.progressFill) waveEls.progressFill.style.width = pct;
    });

    audioPlayer.addEventListener('loadedmetadata', () => {
        const total = formatTime(audioPlayer.duration || 0);
        if (timeTotal) timeTotal.textContent = total;
        if (waveEls && waveEls.timeTotal) waveEls.timeTotal.textContent = total;
    });

    audioPlayer.addEventListener('ended', () => {
        if (currentlyPlayingTrack) sendHistory(currentlyPlayingTrack.id, 'finish', audioPlayer.duration || 0);
        nextTrack(false);
    });

    if (btnPlay) btnPlay.addEventListener('click', togglePlay);
    if (btnNext) btnNext.addEventListener('click', () => nextTrack(true));
    if (btnPrev) btnPrev.addEventListener('click', prevTrack);
    bindSeek(progressTrack);

    if (volumeRange) {
        volumeRange.addEventListener('input', (e) => setAppVolume(parseFloat(e.target.value) / 100));
    }
    if (btnMute) btnMute.addEventListener('click', toggleMute);
    if (btnLikePlayer) btnLikePlayer.addEventListener('click', () => applyLike(currentlyPlayingTrack));

    tracklist.addEventListener('click', (e) => {
        const likeBtn = e.target.closest('.track-like-btn');
        if (!likeBtn) return;
        e.stopPropagation();
        const row = likeBtn.closest('.track-row');
        if (row && row._track) applyLike(row._track);
    });

    async function switchView(view) {
        if (!view) return;
        const seq = ++viewSeq;

        document.querySelectorAll(".nav-item").forEach(el => {
            el.classList.toggle("active", el.dataset.view === view);
        });
        currentView = view;
        waveEls = null;
        document.body.className = `view-${view}`;

        if (view === "wave") {
            if (currentlyPlayingTrack) {
                queueMode = "wave";
                renderWaveView();
            } else {
                await initWavePlayback();
            }
        } else if (view === "search") {
            renderSearchView();
        } else if (view === "library") {
            tracklist.innerHTML = "<div style=\"padding:36px;color:#888;\">Загрузка библиотеки...</div>";
            try {
                const tracks = await fetchTracks({ limit: 100 });
                if (seq !== viewSeq) return;
                renderTracklist(tracks);
            } catch {
                if (seq !== viewSeq) return;
                tracklist.innerHTML = "<div style=\"padding:36px;color:red;\">Ошибка загрузки библиотеки</div>";
            }
        } else if (view === "favorites") {
            tracklist.innerHTML = "<div style=\"padding:36px;color:#888;\">Загрузка любимых треков...</div>";
            try {
                const favs = await fetchFavorites();
                if (seq !== viewSeq) return;
                renderTracklist(favs);
            } catch {
                if (seq !== viewSeq) return;
                tracklist.innerHTML = "<div style=\"padding:36px;color:red;\">Ошибка загрузки избранного</div>";
            }
        }
    }

    document.addEventListener("click", (e) => {
        const nav = e.target.closest(".nav-item");
        if (!nav) return;
        e.preventDefault();
        const targetView = nav.dataset.view || nav.getAttribute("data-view");
        if (targetView) switchView(targetView);
    });

    fetchTracks({ limit: 100 }).then(renderTracklist).catch(() => {});
});

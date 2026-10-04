import PlaybackQueue from './playback_queue.js';

document.addEventListener('DOMContentLoaded', () => {
    const DEFAULT_COVER = '/static/img/default-cover.svg';
    const WAVE_STATUS_IDLE = 'Поток подстраивается под ваши лайки и пропуски';

    // Основные элементы страницы
    const tracklist = document.getElementById('tracklist');
    const viewTitle = document.getElementById('view-title');
    const audioPlayer = document.getElementById('audio-player');

    // Элементы нижнего плеера
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

    // Внутреннее состояние
    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let queueMode = 'list';        // 'list' — очередь из открытого списка, 'wave' — бесконечная «Моя волна»
    let queueListRef = null;       // массив, из которого собрана очередь (не пересобираем без нужды)
    let visibleTracks = [];        // что сейчас показано списком (для кнопки play в доке)
    let isPrefetchingWave = false;
    let autoSkipTimer = null;
    let searchDebounceTimer = null;
    let searchSeq = 0;             // защита от гонки ответов поиска
    let playToken = 0;             // защита от гонки при быстрых кликах по трекам
    let viewSeq = 0;               // защита от гонки при быстром переключении вкладок
    let consecutiveErrors = 0;
    let waveEls = null;            // ссылки на элементы сцены «Моя волна», пока она на экране
    let searchTab = 'popular';
    let picksList = [];

    // =========================================================================
    // Данные витрины поиска
    // =========================================================================
    const SEARCH_TILES = [
        {
            name: 'Осенняя', icon: 'fa-leaf', bg: 'linear-gradient(135deg, #c2410c, #78350f)',
            queries: [
                { label: 'Осенний вайб', q: 'осенняя музыка' },
                { label: 'Дождь и уют', q: 'музыка для дождливой погоды' },
                { label: 'Лирика', q: 'лирические песни' }
            ]
        },
        {
            name: 'Настроения', icon: 'fa-sun', bg: 'linear-gradient(135deg, #f59e0b, #d97706)',
            queries: [
                { label: 'Энергия', q: 'энергичная музыка' },
                { label: 'Спокойствие', q: 'спокойная музыка' },
                { label: 'Грусть', q: 'грустные песни' },
                { label: 'Романтика', q: 'романтичные песни' }
            ]
        },
        {
            name: 'Занятия', icon: 'fa-person-running', bg: 'linear-gradient(135deg, #ef4444, #991b1b)',
            queries: [
                { label: 'Спорт', q: 'музыка для тренировки' },
                { label: 'Работа и учёба', q: 'музыка для концентрации' },
                { label: 'В дорогу', q: 'музыка в дорогу' },
                { label: 'Сон', q: 'музыка для сна' }
            ]
        },
        {
            name: 'Жанры', icon: 'fa-compact-disc', bg: 'linear-gradient(135deg, #ec4899, #be185d)',
            queries: [
                { label: 'Рок', q: 'русский рок' },
                { label: 'Рэп', q: 'русский рэп' },
                { label: 'Поп', q: 'поп хиты' },
                { label: 'Электроника', q: 'электронная музыка' },
                { label: 'Метал', q: 'метал' },
                { label: 'Джаз', q: 'джаз' }
            ]
        },
        {
            name: 'Эпохи', icon: 'fa-clock-rotate-left', bg: 'linear-gradient(135deg, #6366f1, #4338ca)',
            queries: [
                { label: '80-е', q: 'хиты 80-х' },
                { label: '90-е', q: 'хиты 90-х' },
                { label: '2000-е', q: 'хиты 2000-х' },
                { label: '2010-е', q: 'хиты 2010-х' }
            ]
        }
    ];

    const MIX_CARDS = [
        { name: 'Ночной драйв', icon: 'fa-car-side', bg: 'linear-gradient(135deg, #3b82f6, #1d4ed8)', query: 'ночной драйв музыка' },
        { name: 'Русский рэп', icon: 'fa-microphone-lines', bg: 'linear-gradient(135deg, #10b981, #047857)', query: 'русский рэп' },
        { name: 'Иностранный рок', icon: 'fa-guitar', bg: 'linear-gradient(135deg, #8b5cf6, #6d28d9)', query: 'rock hits' },
        { name: 'Электроника', icon: 'fa-bolt', bg: 'linear-gradient(135deg, #06b6d4, #0e7490)', query: 'electronic music' }
    ];

    // =========================================================================
    // Утилиты
    // =========================================================================
    const store = {
        get(key, fallback) {
            try {
                const v = localStorage.getItem(key);
                return v === null ? fallback : v;
            } catch {
                return fallback;
            }
        },
        set(key, value) {
            try { localStorage.setItem(key, value); } catch { /* приватный режим — не критично */ }
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

    function cssUrl(value) {
        return String(value).replace(/["\\\n\r]/g, ch => encodeURIComponent(ch));
    }

    function hasValidId(track) {
        return Boolean(track) && Boolean(track.id) && track.id !== -1 && track.id !== '-1';
    }

    function isSameTrack(a, b) {
        if (!a || !b) return false;
        if (hasValidId(a) && hasValidId(b)) return String(a.id) === String(b.id);
        return a.title === b.title && a.artist === b.artist;
    }

    // Стабильный ключ трека: не меняется, когда внешний трек получает id в БД
    function trackKey(t) {
        if (!t) return '';
        if (t.external_id) return `x:${t.external_id}`;
        if (hasValidId(t)) return `i:${t.id}`;
        return `t:${t.title}|${t.artist}`;
    }

    function setImgWithFallback(img, src) {
        if (!img) return;
        delete img.dataset.fallback;
        img.src = src || DEFAULT_COVER;
    }

    function bindImgFallback(img) {
        img.addEventListener('error', () => {
            if (img.dataset.fallback) return; // без бесконечного цикла, если и заглушки нет
            img.dataset.fallback = '1';
            img.src = DEFAULT_COVER;
        });
    }

    // =========================================================================
    // Сетевые вызовы
    // =========================================================================
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

    // Внешний трек из поиска попадает в БД только когда с ним что-то делают (играют, лайкают)
    const registering = new WeakMap();

    function ensureTrackId(track) {
        if (hasValidId(track)) return Promise.resolve(track.id);
        if (registering.has(track)) return registering.get(track);

        const promise = (async () => {
            const saved = await fetchJson('/tracks/remote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(track)
            });
            track.id = saved.id;
            if (saved.liked !== undefined) track.liked = Boolean(saved.liked);
            return track.id;
        })().finally(() => registering.delete(track));

        registering.set(track, promise);
        return promise;
    }

    // =========================================================================
    // Громкость (с сохранением между сессиями)
    // =========================================================================
    function syncVolumeUI() {
        const muted = audioPlayer.muted || audioPlayer.volume === 0;
        const cls = muted ? 'fas fa-volume-xmark'
            : (audioPlayer.volume < 0.4 ? 'fas fa-volume-low' : 'fas fa-volume-high');
        const dockIcon = btnMute ? btnMute.querySelector('i') : null;
        if (dockIcon) dockIcon.className = cls;
        const waveIcon = waveEls ? waveEls.mute.querySelector('i') : null;
        if (waveIcon) waveIcon.className = cls;
    }

    function toggleMute() {
        audioPlayer.muted = !audioPlayer.muted;
        store.set('player:muted', audioPlayer.muted ? '1' : '0');
        syncVolumeUI();
    }

    (function restoreVolume() {
        const saved = Number(store.get('player:volume', volumeRange ? volumeRange.value : '70'));
        const percent = Math.min(100, Math.max(0, isFinite(saved) ? saved : 70));
        if (volumeRange) volumeRange.value = String(percent);
        // раньше ползунок показывал 70, а звук играл на 100: громкость не применялась до первого движения
        audioPlayer.volume = percent / 100;
        audioPlayer.muted = store.get('player:muted', '0') === '1';
        syncVolumeUI();
    })();

    // =========================================================================
    // Перемотка (клик и перетаскивание)
    // =========================================================================
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
            try { trackEl.setPointerCapture(e.pointerId); } catch { /* не критично */ }
            seekTo(e.clientX);
            const move = (ev) => seekTo(ev.clientX);
            const up = () => {
                trackEl.removeEventListener('pointermove', move);
                trackEl.removeEventListener('pointerup', up);
                trackEl.removeEventListener('pointercancel', up);
            };
            trackEl.addEventListener('pointermove', move);
            trackEl.addEventListener('pointerup', up);
            trackEl.addEventListener('pointercancel', up);
        });
    }

    // =========================================================================
    // Состояние «играет / на паузе» и «нравится» во всех местах интерфейса
    // =========================================================================
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
        if (waveEls) {
            const icon = waveEls.play.querySelector('i');
            icon.className = isPlaying ? 'fas fa-pause' : 'fas fa-play';
            icon.style.marginLeft = isPlaying ? '0' : '4px';
            waveEls.screen.classList.toggle('playing', isPlaying);
        }
        updateActiveTrackUI();
    }

    function syncLikeUI() {
        setHeartIcon(btnLikePlayer, currentlyPlayingTrack && currentlyPlayingTrack.liked);
        if (waveEls && currentlyPlayingTrack) setHeartIcon(waveEls.like, currentlyPlayingTrack.liked);
        tracklist.querySelectorAll('.track-row').forEach(row => {
            if (row._track) setHeartIcon(row.querySelector('.track-like-btn'), row._track.liked);
        });
    }

    const likeBusy = new WeakSet();

    async function applyLike(track) {
        if (!track || likeBusy.has(track)) return;
        likeBusy.add(track);
        try {
            const wasLiked = Boolean(track.liked);
            await ensureTrackId(track);
            await toggleLike(track.id, wasLiked);
            track.liked = !wasLiked;
            // тот же трек мог прийти из другого списка отдельным объектом
            if (currentlyPlayingTrack && currentlyPlayingTrack !== track && isSameTrack(currentlyPlayingTrack, track)) {
                currentlyPlayingTrack.liked = track.liked;
            }
            tracklist.querySelectorAll('.track-row').forEach(row => {
                if (row._track && row._track !== track && isSameTrack(row._track, track)) row._track.liked = track.liked;
            });
            syncLikeUI();
        } catch (err) {
            console.error('Ошибка лайка:', err);
            showToast('⚠️ Не удалось изменить «Нравится»');
        } finally {
            likeBusy.delete(track);
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

    // =========================================================================
    // Сцена «Моя волна»
    // Собирается один раз и дальше обновляется на месте: без мерцания на каждой
    // смене трека и без повторной навески обработчиков.
    // =========================================================================
    function setWaveStatus(text) {
        if (waveEls) waveEls.status.textContent = text;
    }

    function renderWaveIdle({ loading = false } = {}) {
        waveEls = null;
        if (viewTitle) viewTitle.textContent = '';
        tracklist.innerHTML = `
            <div class="wave-screen is-idle">
                <div class="wave-glow"></div>
                <div class="wave-stage">
                    <span class="wave-label">Бесконечный поток</span>
                    <h2 class="wave-idle-title">Моя волна</h2>
                    <button id="btn-wave-start" class="wave-btn-circle" title="Включить волну" ${loading ? 'disabled' : ''}>
                        ${loading
                            ? '<i class="fas fa-spinner fa-spin"></i>'
                            : '<i class="fas fa-play" style="margin-left:4px;"></i>'}
                    </button>
                    <p class="wave-idle-note">${loading ? 'Изучаю звучание…' : 'Нажмите, чтобы запустить бесконечный поток музыки'}</p>
                </div>
            </div>
        `;
        document.getElementById('btn-wave-start')?.addEventListener('click', initWavePlayback);
    }

    function mountWaveScreen() {
        if (waveEls && waveEls.screen.isConnected) return;
        tracklist.innerHTML = `
            <div class="wave-screen">
                <div class="wave-backdrop" id="wave-backdrop"></div>
                <div class="wave-glow"></div>
                <div class="wave-stage" id="wave-stage">
                    <span class="wave-label">Моя волна</span>
                    <h2 class="wave-artist" id="wave-artist"></h2>
                    <button class="wave-cover-btn" id="wave-cover-btn" title="Играть / пауза">
                        <img id="wave-cover" alt="Обложка" src="${DEFAULT_COVER}">
                    </button>

                    <div class="wave-actions">
                        <button id="wave-mute-btn" class="wave-round" title="Звук"><i class="fas fa-volume-high"></i></button>
                        <button id="wave-dislike-btn" class="wave-round" title="Не нравится — пропустить"><i class="fas fa-ban"></i></button>
                        <div class="wave-capsule"><span id="wave-title"></span></div>
                        <button id="wave-like-btn" class="wave-round" title="Нравится"><i class="far fa-heart"></i></button>
                    </div>

                    <div class="wave-progress">
                        <span class="progress-time" id="wave-time-current">0:00</span>
                        <div class="progress-track" id="wave-progress-track"><div class="progress-fill" id="wave-progress-fill"></div></div>
                        <span class="progress-time" id="wave-time-total">0:00</span>
                    </div>

                    <div class="wave-transport">
                        <button id="wave-prev-btn" class="wave-btn-side" title="Предыдущий"><i class="fas fa-backward-step"></i></button>
                        <button id="wave-play-toggle" class="wave-btn-circle" title="Воспроизведение / пауза"><i class="fas fa-play" style="margin-left:4px;"></i></button>
                        <button id="wave-next-btn" class="wave-btn-side" title="Следующий"><i class="fas fa-forward-step"></i></button>
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
            play: $('wave-play-toggle'),
            timeCur: $('wave-time-current'),
            timeTotal: $('wave-time-total'),
            progressTrack: $('wave-progress-track'),
            progressFill: $('wave-progress-fill'),
            status: $('wave-status-text'),
            lastKey: null
        };

        bindImgFallback(waveEls.cover);
        bindSeek(waveEls.progressTrack);

        $('wave-cover-btn').addEventListener('click', togglePlay);
        waveEls.play.addEventListener('click', togglePlay);
        $('wave-next-btn').addEventListener('click', () => nextTrack(true));
        $('wave-prev-btn').addEventListener('click', prevTrack);
        waveEls.mute.addEventListener('click', toggleMute);
        waveEls.like.addEventListener('click', () => applyLike(currentlyPlayingTrack));
        $('wave-dislike-btn').addEventListener('click', () => {
            if (currentlyPlayingTrack) sendHistory(currentlyPlayingTrack.id, 'skip', 1.0);
            nextTrack(false);
        });
        $('wave-shake-btn').addEventListener('click', initWavePlayback);

        syncVolumeUI();
    }

    function updateWaveScreen() {
        const t = currentlyPlayingTrack;
        if (!t || !waveEls) return;

        const key = trackKey(t);
        const changed = waveEls.lastKey !== key;
        waveEls.lastKey = key;

        const cover = t.cover_path || DEFAULT_COVER;
        waveEls.artist.textContent = t.artist || 'Неизвестный исполнитель';
        waveEls.title.textContent = t.title || 'Без названия';
        waveEls.title.title = t.title || '';

        if (changed) {
            setImgWithFallback(waveEls.cover, cover);
            waveEls.backdrop.style.backgroundImage = t.cover_path ? `url("${cssUrl(t.cover_path)}")` : 'none';
            waveEls.stage.classList.remove('swap');
            void waveEls.stage.offsetWidth; // перезапуск CSS-анимации
            waveEls.stage.classList.add('swap');
            waveEls.progressFill.style.width = '0%';
            waveEls.timeCur.textContent = '0:00';
            waveEls.timeTotal.textContent = formatTime(t.duration);
        }

        setHeartIcon(waveEls.like, t.liked);
        syncPlayPauseUI(!audioPlayer.paused);
    }

    function renderWaveView() {
        if (!tracklist) return;
        if (queueMode === 'wave' && currentlyPlayingTrack) {
            mountWaveScreen();
            updateWaveScreen();
        } else {
            renderWaveIdle();
        }
    }

    async function initWavePlayback() {
        if (currentView === 'wave') renderWaveIdle({ loading: true });
        setWaveStatus('Изучаю звучание…');
        try {
            const tracks = await fetchWave(20);
            if (tracks.length === 0) {
                showToast('Волна пока пуста — добавьте музыку или лайки');
                if (currentView === 'wave') renderWaveIdle();
                return;
            }
            queueMode = 'wave';
            queueListRef = null;
            playbackQueue.setQueue(tracks);
            await playTrack(0, { force: true });
            if (currentView === 'wave') renderWaveView();
        } catch (e) {
            console.error('Ошибка запуска волны:', e);
            showToast('⚠️ Не удалось загрузить волну');
            if (currentView === 'wave' && !(queueMode === 'wave' && currentlyPlayingTrack)) renderWaveIdle();
            else setWaveStatus(WAVE_STATUS_IDLE);
        }
    }

    async function prefetchWaveIfNeeded() {
        if (queueMode !== 'wave' || isPrefetchingWave) return;
        if (playbackQueue.length - playbackQueue.index > 3) return;
        isPrefetchingWave = true;
        setWaveStatus('Подбираю следующие треки…');
        try {
            const extra = await fetchWave(12);
            const known = new Set(playbackQueue.queue.map(trackKey));
            const fresh = extra.filter(t => !known.has(trackKey(t)));
            // если волна вернула только уже игравшее — лучше повторы, чем тишина
            playbackQueue.appendTracks(fresh.length ? fresh : extra);
        } catch (err) {
            console.error('Ошибка префетча волны:', err);
        } finally {
            isPrefetchingWave = false;
            setWaveStatus(WAVE_STATUS_IDLE);
        }
    }

    // =========================================================================
    // Поиск: вытянутая строка, вкладки «Популярное / История», плитки, выдача
    // =========================================================================
    function getSearchHistory() {
        try {
            const arr = JSON.parse(store.get('player:searchHistory', '[]'));
            return Array.isArray(arr) ? arr.filter(s => typeof s === 'string') : [];
        } catch {
            return [];
        }
    }

    function saveSearchHistory(items) {
        store.set('player:searchHistory', JSON.stringify(items.slice(0, 15)));
    }

    function rememberQuery(query) {
        const q = (query || '').trim();
        if (!q) return;
        const rest = getSearchHistory().filter(s => s.toLowerCase() !== q.toLowerCase());
        saveSearchHistory([q, ...rest]);
    }

    function renderSearchView() {
        if (viewTitle) viewTitle.textContent = '';
        searchTab = 'popular';
        tracklist.innerHTML = `
            <div class="search-view" id="search-view">
                <div class="search-bar">
                    <i class="fas fa-search search-input-icon"></i>
                    <input type="search" id="search-input" class="search-input-field"
                           placeholder="Трек, исполнитель, альбом или настроение" autocomplete="off" spellcheck="false">
                    <button class="search-clear" id="search-clear" hidden aria-label="Очистить"><i class="fas fa-xmark"></i></button>
                </div>
                <div class="search-tabs" id="search-tabs">
                    <button class="search-tab active" data-tab="popular">Популярное</button>
                    <button class="search-tab" data-tab="history">История</button>
                </div>
                <div class="search-zone" id="search-results-zone"></div>
            </div>
        `;

        const view = document.getElementById('search-view');
        const inputEl = document.getElementById('search-input');
        const clearBtn = document.getElementById('search-clear');
        const zone = document.getElementById('search-results-zone');

        const syncChrome = () => {
            const has = inputEl.value.trim().length > 0;
            view.classList.toggle('has-query', has);
            clearBtn.hidden = inputEl.value.length === 0;
        };

        inputEl.addEventListener('input', () => {
            clearTimeout(searchDebounceTimer);
            const query = inputEl.value.trim();
            syncChrome();
            if (!query) {
                renderSearchHome();
                return;
            }
            searchDebounceTimer = setTimeout(() => executeSearch(query), 300);
        });

        inputEl.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            const query = inputEl.value.trim();
            if (!query) return;
            clearTimeout(searchDebounceTimer);
            executeSearch(query, { remember: true });
        });

        clearBtn.addEventListener('click', () => {
            inputEl.value = '';
            clearTimeout(searchDebounceTimer);
            syncChrome();
            renderSearchHome();
            inputEl.focus();
        });

        document.getElementById('search-tabs').addEventListener('click', (e) => {
            const tab = e.target.closest('[data-tab]');
            if (!tab) return;
            searchTab = tab.dataset.tab;
            document.querySelectorAll('#search-tabs .search-tab').forEach(el => {
                el.classList.toggle('active', el === tab);
            });
            renderSearchHome();
        });

        zone.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('[data-history-remove]');
            if (removeBtn) {
                const q = removeBtn.dataset.historyRemove;
                saveSearchHistory(getSearchHistory().filter(s => s !== q));
                renderSearchHome();
                return;
            }
            if (e.target.closest('[data-history-clear]')) {
                saveSearchHistory([]);
                renderSearchHome();
                return;
            }
            const tile = e.target.closest('[data-tile]');
            if (tile) {
                toggleTile(tile);
                return;
            }
            const pick = e.target.closest('[data-pick]');
            if (pick) {
                playFromList(picksList, Number(pick.dataset.pick));
                return;
            }
            const queryEl = e.target.closest('[data-query]');
            if (queryEl) {
                inputEl.value = queryEl.dataset.query;
                syncChrome();
                executeSearch(queryEl.dataset.query, { remember: true });
            }
        });

        renderSearchHome();
        inputEl.focus();
    }

    function toggleTile(tileEl) {
        const panel = document.getElementById('chip-panel');
        if (!panel) return;
        const wasOpen = tileEl.classList.contains('open');
        document.querySelectorAll('.tile.open').forEach(el => el.classList.remove('open'));
        if (wasOpen) {
            panel.innerHTML = '';
            return;
        }
        tileEl.classList.add('open');
        const tile = SEARCH_TILES[Number(tileEl.dataset.tile)];
        panel.innerHTML = tile.queries
            .map(item => `<button class="search-chip" data-query="${escapeHtml(item.q)}">${escapeHtml(item.label)}</button>`)
            .join('');
    }

    function renderSearchHome() {
        const zone = document.getElementById('search-results-zone');
        if (!zone) return;
        searchSeq++; // отменяем ещё не вернувшийся поиск
        zone.classList.remove('search-results');

        if (searchTab === 'history') {
            const items = getSearchHistory();
            if (items.length === 0) {
                zone.innerHTML = '<div class="empty-note">История поиска пока пуста</div>';
                return;
            }
            zone.innerHTML = `
                <div>
                    <div class="history-head">
                        <h3 class="section-title" style="margin:0;">Недавние запросы</h3>
                        <button class="history-clear" data-history-clear>Очистить</button>
                    </div>
                    ${items.map(q => `
                        <div class="history-item" data-query="${escapeHtml(q)}">
                            <i class="fas fa-clock-rotate-left"></i>
                            <span class="history-item-text">${escapeHtml(q)}</span>
                            <button class="history-item-remove" data-history-remove="${escapeHtml(q)}" aria-label="Удалить из истории"><i class="fas fa-xmark"></i></button>
                        </div>
                    `).join('')}
                </div>
            `;
            return;
        }

        zone.innerHTML = `
            <section>
                <div class="tile-row">
                    ${SEARCH_TILES.map((t, i) => `
                        <button class="tile" data-tile="${i}">
                            <span class="tile-art" style="--tile-bg: ${t.bg};"><i class="fas ${t.icon}"></i></span>
                            <span class="tile-name">${t.name}</span>
                        </button>
                    `).join('')}
                </div>
                <div class="chip-panel" id="chip-panel"></div>
            </section>
            <section id="picks-section" hidden>
                <h3 class="section-title">Из вашей коллекции</h3>
                <div class="pick-grid" id="pick-grid"></div>
            </section>
            <section>
                <h3 class="section-title">Вы могли пропустить</h3>
                <div class="mix-row">
                    ${MIX_CARDS.map(c => `
                        <button class="mix-card" style="background: ${c.bg};" data-query="${escapeHtml(c.query)}">
                            <span class="mix-card-title">${c.name}</span>
                            <i class="fas ${c.icon} mix-card-icon"></i>
                        </button>
                    `).join('')}
                </div>
            </section>
        `;
        loadPicks();
    }

    async function loadPicks() {
        const seq = searchSeq;
        try {
            const favs = await fetchFavorites({ limit: 6 });
            if (seq !== searchSeq || !Array.isArray(favs) || favs.length === 0) return;
            const grid = document.getElementById('pick-grid');
            const section = document.getElementById('picks-section');
            if (!grid || !section) return;
            picksList = favs;
            grid.innerHTML = favs.map((t, i) => `
                <div class="pick-card" data-pick="${i}">
                    <img src="${escapeHtml(t.cover_path || DEFAULT_COVER)}" alt="">
                    <div class="pick-card-titles">
                        <span class="pick-card-title">${escapeHtml(t.title || 'Без названия')}</span>
                        <span class="pick-card-artist">${escapeHtml(t.artist || 'Неизвестный исполнитель')}</span>
                    </div>
                    <button class="pick-card-play" aria-label="Играть"><i class="fas fa-play"></i></button>
                </div>
            `).join('');
            grid.querySelectorAll('img').forEach(bindImgFallback);
            section.hidden = false;
        } catch {
            /* витрина без коллекции — не ошибка */
        }
    }

    async function executeSearch(query, { remember = false } = {}) {
        const zone = document.getElementById('search-results-zone');
        if (!zone) return;
        const seq = ++searchSeq;

        zone.classList.add('search-results');
        zone.innerHTML = '<div class="empty-note"><i class="fas fa-spinner fa-spin"></i> Поиск…</div>';

        try {
            const results = await searchTracks(query);
            if (seq !== searchSeq || currentView !== 'search') return; // пришёл устаревший ответ
            if (!results || results.length === 0) {
                zone.innerHTML = '<div class="empty-note">Ничего не найдено</div>';
                return;
            }
            if (remember) rememberQuery(query);

            const groups = {};
            results.forEach(track => {
                let label = 'YouTube Music';
                if (track.source === 'local') label = 'Локально';
                else if (track.source === 'soundcloud') label = 'SoundCloud';
                (groups[label] = groups[label] || []).push(track);
            });
            const order = ['Локально', 'YouTube Music', 'SoundCloud'];
            const labels = Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b));
            // очередь = порядок на экране, поэтому «следующий» всегда тот, что ниже
            const ordered = labels.flatMap(label => groups[label]);

            visibleTracks = ordered;
            const wrapper = document.createElement('div');
            wrapper.className = 'track-list-wrapper';
            let offset = 0;
            labels.forEach(label => {
                const heading = document.createElement('h4');
                heading.className = 'source-group-heading';
                heading.textContent = label;
                wrapper.appendChild(heading);
                groups[label].forEach((track, i) => {
                    wrapper.appendChild(createTrackRow(track, ordered, offset + i, { compact: true }));
                });
                offset += groups[label].length;
            });
            zone.innerHTML = '';
            zone.appendChild(wrapper);
            updateActiveTrackUI();
        } catch (err) {
            if (seq !== searchSeq) return;
            console.error('Ошибка поиска:', err);
            zone.innerHTML = '<div class="error-note">Ошибка выполнения поиска</div>';
        }
    }

    // =========================================================================
    // Списки треков (Библиотека / Избранное / выдача поиска)
    // =========================================================================
    function createTrackRow(track, list, index, { compact = false } = {}) {
        const row = document.createElement('div');
        row.className = `track-row${compact ? ' track-row--compact' : ''}`;
        row._track = track;

        const durationText = track.duration ? formatTime(track.duration) : '--:--';

        row.innerHTML = `
            ${compact ? '' : `<span class="track-row-index">${index + 1}</span>`}
            <div class="track-row-cover-wrap">
                <img src="${escapeHtml(track.cover_path || DEFAULT_COVER)}" alt="" class="track-row-cover" loading="lazy">
                <div class="track-row-overlay">
                    <i class="fas fa-play"></i>
                    <span class="eq"><span></span><span></span><span></span></span>
                </div>
            </div>
            <div class="track-row-titles">
                <span class="track-row-title">${escapeHtml(track.title || 'Без названия')}</span>
                <span class="track-row-artist">${escapeHtml(track.artist || 'Неизвестный исполнитель')}</span>
            </div>
            ${compact ? '' : `<span class="track-row-album">${escapeHtml(track.album || '-')}</span>`}
            <span class="track-row-duration">${durationText}</span>
            <div class="track-row-actions">
                <button class="track-like-btn ${track.liked ? 'liked' : ''}" data-id="${escapeHtml(track.id ?? '')}" aria-label="Нравится">
                    <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                </button>
            </div>
        `;

        bindImgFallback(row.querySelector('.track-row-cover'));
        row.addEventListener('click', (e) => {
            if (e.target.closest('.track-like-btn')) return;
            playFromList(list, index);
        });
        if (isSameTrack(currentlyPlayingTrack, track)) {
            row.classList.add('active');
            if (!audioPlayer.paused) row.classList.add('playing');
        }
        return row;
    }

    function renderTracklist(tracks, emptyText = 'Треки не найдены') {
        visibleTracks = Array.isArray(tracks) ? tracks : [];
        tracklist.innerHTML = '';
        if (visibleTracks.length === 0) {
            tracklist.innerHTML = `<div class="empty-note" style="padding:36px;">${escapeHtml(emptyText)}</div>`;
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'track-list-wrapper';
        visibleTracks.forEach((track, index) => {
            wrapper.appendChild(createTrackRow(track, visibleTracks, index));
        });
        tracklist.appendChild(wrapper);
    }

    // =========================================================================
    // Воспроизведение
    // =========================================================================
    function paintNowPlaying(track) {
        playerTitle.textContent = track.title || 'Без названия';
        playerArtist.textContent = track.artist || 'Неизвестный исполнитель';
        setImgWithFallback(playerCover, track.cover_path);
        setHeartIcon(btnLikePlayer, track.liked);
        // раньше прогресс прошлого трека висел, пока не придёт первый timeupdate
        if (progressFill) progressFill.style.width = '0%';
        if (timeCurrent) timeCurrent.textContent = '0:00';
        if (timeTotal) timeTotal.textContent = formatTime(track.duration);
        if (currentView === 'wave') renderWaveView();
        updateActiveTrackUI();
    }

    // Очередь собирается из открытого списка в момент клика, а не при каждой отрисовке:
    // просмотр библиотеки или поиска не должен ломать играющую волну.
    function playFromList(list, index) {
        if (!Array.isArray(list) || index < 0 || index >= list.length) return;
        if (queueMode !== 'list' || queueListRef !== list) {
            queueMode = 'list';
            queueListRef = list;
            playbackQueue.setQueue(list);
        }
        if (currentView === 'search') {
            const input = document.getElementById('search-input');
            if (input && input.value.trim()) rememberQuery(input.value);
        }
        return playTrack(index);
    }

    async function playTrack(index, { force = false } = {}) {
        if (index < 0 || index >= playbackQueue.length) return;
        clearTimeout(autoSkipTimer);
        const track = playbackQueue.queue[index];

        if (!force && isSameTrack(currentlyPlayingTrack, track) && audioPlayer.src && !audioPlayer.error) {
            if (audioPlayer.paused) {
                try {
                    await audioPlayer.play();
                } catch (e) {
                    console.warn('Play failed:', e);
                }
            } else {
                audioPlayer.pause();
            }
            return;
        }

        const token = ++playToken;
        playbackQueue.playAt(index);
        currentlyPlayingTrack = track;
        paintNowPlaying(track); // интерфейс откликается сразу, не дожидаясь сети

        if (!hasValidId(track)) {
            try {
                await ensureTrackId(track);
            } catch {
                if (token !== playToken) return;
                showToast('⚠️ Ошибка регистрации внешнего трека');
                autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                return;
            }
            if (token !== playToken) return; // за это время запустили другой трек
            setHeartIcon(btnLikePlayer, track.liked);
        }

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(e => {
            console.warn('Play interrupted or pending:', e);
            syncPlayPauseUI(false);
        });

        sendHistory(track.id, 'play', 0);
        prefetchWaveIfNeeded();
    }

    async function togglePlay() {
        if (!currentlyPlayingTrack) {
            if (visibleTracks.length > 0) playFromList(visibleTracks, 0);
            else if (playbackQueue.length > 0) playTrack(0);
            return;
        }
        if (audioPlayer.paused) {
            if (audioPlayer.error || !audioPlayer.src || audioPlayer.src.endsWith('/-1')) {
                if (playbackQueue.index >= 0) {
                    playTrack(playbackQueue.index, { force: true });
                    return;
                }
            }
            try {
                await audioPlayer.play();
            } catch (err) {
                console.warn('audioPlayer.play() failed:', err);
                syncPlayPauseUI(false);
                if (audioPlayer.error) {
                    showToast('⚠️ Ошибка источника. Переключаем…');
                    autoSkipTimer = setTimeout(() => nextTrack(false), 1200);
                }
            }
        } else {
            audioPlayer.pause();
        }
    }

    function nextTrack(isSkip = false) {
        clearTimeout(autoSkipTimer);
        if (isSkip && currentlyPlayingTrack) {
            const elapsed = audioPlayer.currentTime || 0;
            if (elapsed < 15) sendHistory(currentlyPlayingTrack.id, 'skip', elapsed);
        }
        if (playbackQueue.next()) {
            playTrack(playbackQueue.index);
            return;
        }
        if (queueMode === 'wave') {
            prefetchWaveIfNeeded().then(() => {
                if (playbackQueue.next()) {
                    playTrack(playbackQueue.index);
                } else {
                    showToast('Волна пока не смогла подобрать продолжение');
                }
            });
        }
    }

    function prevTrack() {
        clearTimeout(autoSkipTimer);
        if (playbackQueue.previous()) {
            playTrack(playbackQueue.index);
        }
    }

    // =========================================================================
    // Слушатели событий плеера
    // =========================================================================
    audioPlayer.addEventListener('play', () => syncPlayPauseUI(true));
    audioPlayer.addEventListener('pause', () => syncPlayPauseUI(false));
    audioPlayer.addEventListener('playing', () => { consecutiveErrors = 0; });

    audioPlayer.addEventListener('error', (e) => {
        console.error('Ошибка аудиопотока:', e, audioPlayer.error);
        syncPlayPauseUI(false);
        clearTimeout(autoSkipTimer);
        consecutiveErrors += 1;
        if (consecutiveErrors > 4) {
            consecutiveErrors = 0;
            showToast('⚠️ Несколько треков подряд не воспроизводятся — проверьте сервер');
            return;
        }
        showToast('⚠️ Трек недоступен. Переключаем…');
        autoSkipTimer = setTimeout(() => nextTrack(false), 1800);
    });

    audioPlayer.addEventListener('timeupdate', () => {
        const cur = audioPlayer.currentTime;
        const dur = audioPlayer.duration || 0;
        const pct = dur > 0 && isFinite(dur) ? `${(cur / dur) * 100}%` : null;
        if (timeCurrent) timeCurrent.textContent = formatTime(cur);
        if (pct && progressFill) progressFill.style.width = pct;
        if (waveEls) {
            waveEls.timeCur.textContent = formatTime(cur);
            if (pct) waveEls.progressFill.style.width = pct;
        }
    });

    audioPlayer.addEventListener('loadedmetadata', () => {
        const total = formatTime(audioPlayer.duration || 0);
        if (timeTotal) timeTotal.textContent = total;
        if (waveEls) waveEls.timeTotal.textContent = total;
    });

    audioPlayer.addEventListener('ended', () => {
        if (currentlyPlayingTrack) {
            sendHistory(currentlyPlayingTrack.id, 'finish', audioPlayer.duration || 0);
        }
        nextTrack(false);
    });

    if (btnPlay) btnPlay.addEventListener('click', togglePlay);
    if (btnNext) btnNext.addEventListener('click', () => nextTrack(true));
    if (btnPrev) btnPrev.addEventListener('click', prevTrack);
    bindSeek(progressTrack);

    if (volumeRange) {
        volumeRange.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            audioPlayer.volume = val / 100;
            audioPlayer.muted = false;
            store.set('player:volume', String(val));
            store.set('player:muted', '0');
            syncVolumeUI();
        });
    }
    if (btnMute) btnMute.addEventListener('click', toggleMute);

    // Лайк из нижнего плеера
    if (btnLikePlayer) {
        btnLikePlayer.addEventListener('click', () => applyLike(currentlyPlayingTrack));
    }

    // Лайк из списков (делегирование: строки перерисовываются)
    tracklist.addEventListener('click', (e) => {
        const likeBtn = e.target.closest('.track-like-btn');
        if (!likeBtn) return;
        e.stopPropagation();
        const row = likeBtn.closest('.track-row');
        if (row && row._track) applyLike(row._track);
    });

    // =========================================================================
    // Навигация (переключение вкладок без прерывания аудио)
    // =========================================================================
    async function switchView(view) {
        if (!view || view === currentView) return;
        const seq = ++viewSeq;

        document.querySelectorAll('.nav-item').forEach(el => {
            el.classList.toggle('active', el.dataset.view === view);
        });
        currentView = view;
        waveEls = null; // сцена волны живёт только пока открыта её вкладка
        document.body.className = `view-${view}`; // от этого класса зависит скрытие нижнего плеера

        if (view === 'wave') {
            if (queueMode === 'wave' && currentlyPlayingTrack) {
                renderWaveView();
            } else {
                await initWavePlayback();
            }
        } else if (view === 'search') {
            renderSearchView();
        } else if (view === 'library') {
            if (viewTitle) viewTitle.textContent = 'Библиотека';
            try {
                const tracks = await fetchTracks({ limit: 100 });
                if (seq !== viewSeq) return;
                renderTracklist(tracks);
            } catch {
                if (seq !== viewSeq) return;
                tracklist.innerHTML = '<div class="error-note" style="padding:36px;">Ошибка загрузки библиотеки</div>';
            }
        } else if (view === 'favorites') {
            if (viewTitle) viewTitle.textContent = 'Любимые треки';
            try {
                const favs = await fetchFavorites();
                if (seq !== viewSeq) return;
                renderTracklist(favs, 'Пока нет любимых треков — нажмите ♥ на любом треке');
            } catch {
                if (seq !== viewSeq) return;
                tracklist.innerHTML = '<div class="error-note" style="padding:36px;">Ошибка загрузки избранного</div>';
            }
        }
    }

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            switchView(item.dataset.view);
        });
    });

    // Стартовая инициализация
    fetchTracks({ limit: 100 }).then(tracks => renderTracklist(tracks)).catch(() => {
        tracklist.innerHTML = '<div class="empty-note" style="padding:36px;">Ошибка загрузки медиатеки</div>';
    });
});

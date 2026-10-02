import PlaybackQueue from './playback_queue.js';

document.addEventListener('DOMContentLoaded', () => {
    // Основные элементы страницы
    const tracklist = document.getElementById('tracklist');
    const viewTitle = document.getElementById('view-title');
    const audioPlayer = document.getElementById('audio-player');
    const playerDock = document.getElementById('player-dock');

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
    const volumeIcon = btnMute ? btnMute.querySelector('i') : null;

    // Внутреннее состояние
    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let isPrefetchingWave = false;
    let autoSkipTimer = null;
    let searchDebounceTimer = null;

    // Наборы полок для витрины поиска
    const SEARCH_SHELVES = [
        {
            title: "Подборки музыки",
            cards: [
                { name: "Осенняя", icon: "fa-leaf", bg: "linear-gradient(135deg, #c2410c, #78350f)" },
                { name: "Настроения", icon: "fa-sun", bg: "linear-gradient(135deg, #f59e0b, #d97706)" },
                { name: "Для занятий", icon: "fa-person-running", bg: "linear-gradient(135deg, #ef4444, #991b1b)" },
                { name: "Жанры", icon: "fa-compact-disc", bg: "linear-gradient(135deg, #ec4899, #be185d)" },
                { name: "Эпохи", icon: "fa-history", bg: "linear-gradient(135deg, #6366f1, #4338ca)" }
            ]
        },
        {
            title: "Вы могли пропустить",
            cards: [
                { name: "Ночной драйв", icon: "fa-car-side", bg: "linear-gradient(135deg, #3b82f6, #1d4ed8)" },
                { name: "Русский рэп", icon: "fa-microphone-lines", bg: "linear-gradient(135deg, #10b981, #047857)" },
                { name: "Иностранный рок", icon: "fa-guitar", bg: "linear-gradient(135deg, #8b5cf6, #6d28d9)" },
                { name: "Электроника", icon: "fa-bolt", bg: "linear-gradient(135deg, #06b6d4, #0e7490)" }
            ]
        }
    ];

    function showToast(msg) {
        let toast = document.getElementById('player-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'player-toast';
            toast.style.cssText = 'position:fixed;bottom:104px;left:50%;transform:translateX(-50%);background:rgba(18,20,26,0.95);color:#ffcc00;padding:10px 24px;border-radius:24px;font-size:14px;font-weight:600;z-index:9999;pointer-events:none;transition:opacity 0.25s ease;border:1px solid rgba(255,255,255,0.12);box-shadow:0 8px 32px rgba(0,0,0,0.6);';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.style.opacity = '1';
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 2400);
    }

    function formatTime(seconds) {
        if (!seconds || isNaN(seconds)) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function isSameTrack(a, b) {
        if (!a || !b) return false;
        if (a.id && b.id && a.id !== -1 && b.id !== -1 && a.id !== '-1' && b.id !== '-1') {
            return a.id === b.id;
        }
        return a.title === b.title && a.artist === b.artist;
    }

    function syncPlayPauseUI(isPlaying) {
        if (btnPlay) {
            btnPlay.querySelector('i').className = isPlaying ? 'fas fa-pause-circle' : 'fas fa-play-circle';
        }
        const wavePlayBtn = document.getElementById('wave-play-toggle');
        if (wavePlayBtn) {
            wavePlayBtn.querySelector('i').className = isPlaying ? 'fas fa-pause' : 'fas fa-play';
            wavePlayBtn.querySelector('i').style.marginLeft = isPlaying ? '0' : '4px';
        }
        const waveScreen = document.querySelector('.wave-screen');
        if (waveScreen) {
            waveScreen.classList.toggle('playing', isPlaying);
        }
        updateActiveTrackUI();
    }

    // =========================================================================
    // Сетевые вызовы
    // =========================================================================
    async function fetchTracks(params = {}) {
        const query = new URLSearchParams(params).toString();
        const res = await fetch(`/tracks?${query}`);
        if (!res.ok) throw new Error('Ошибка загрузки треков');
        return await res.json();
    }

    async function fetchFavorites() {
        const res = await fetch('/favorites');
        if (!res.ok) throw new Error('Ошибка загрузки избранного');
        return await res.json();
    }

    async function searchTracks(query) {
        const res = await fetch(`/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error('Ошибка поиска');
        return await res.json();
    }

    async function toggleLike(trackId, isLiked) {
        const res = await fetch(`/tracks/${trackId}/like?liked=${!isLiked}`, { method: 'POST' });
        if (!res.ok) throw new Error('Ошибка изменения статуса');
        return await res.json();
    }

    function sendHistory(trackId, event, elapsedSeconds = 0) {
        if (!trackId || trackId === -1 || trackId === '-1') return;
        fetch('/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_id: trackId,
                event: event,
                elapsed_seconds: elapsedSeconds
            })
        }).catch(() => {});
    }

    async function prefetchWaveIfNeeded() {
        if (isPrefetchingWave) return;
        if (playbackQueue.length - playbackQueue.index <= 3) {
            isPrefetchingWave = true;
            try {
                const res = await fetch('/wave?limit=12');
                if (res.ok) {
                    const extra = await res.json();
                    playbackQueue.appendTracks(extra);
                }
            } catch (err) {
                console.error("Ошибка префетча волны:", err);
            } finally {
                isPrefetchingWave = false;
            }
        }
    }

    // =========================================================================
    // Отрисовка сцены «Моя волна» (Центральный виджет с обложкой)
    // =========================================================================
    function renderWaveView() {
        if (!tracklist) return;
        if (viewTitle) viewTitle.textContent = '';

        const track = currentlyPlayingTrack || playbackQueue.getCurrentTrack();
        const isPlaying = !audioPlayer.paused && Boolean(currentlyPlayingTrack) && !audioPlayer.error;

        if (!track) {
            tracklist.innerHTML = `
                <div class="wave-screen">
                    <div class="wave-orb-wrapper">
                        <div class="wave-orb-canvas"></div>
                        <h2 style="position:relative;z-index:2;font-size:2.4rem;font-weight:900;">Моя волна</h2>
                    </div>
                    <div class="wave-controls">
                        <button id="btn-wave-start" class="wave-btn-circle" title="Включить волну">
                            <i class="fas fa-play" style="margin-left: 4px;"></i>
                        </button>
                    </div>
                    <div class="wave-footer-note">
                        <i class="fas fa-sparkles" style="color: var(--accent);"></i>
                        <span>Нажмите, чтобы запустить бесконечный поток музыки</span>
                    </div>
                </div>
            `;
            document.getElementById('btn-wave-start')?.addEventListener('click', async () => {
                const btn = document.getElementById('btn-wave-start');
                if (btn) btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
                await initWavePlayback();
            });
            return;
        }

        const coverSrc = escapeHtml(track.cover_path || '/static/img/default-cover.svg');

        tracklist.innerHTML = `
            <div class="wave-screen ${isPlaying ? 'playing' : ''}">
                <div class="wave-orb-wrapper">
                    <div class="wave-orb-canvas"></div>
                    <div class="wave-track-preview-container">
                        <img src="${coverSrc}" alt="Cover" class="wave-track-preview-img" onerror="this.src='/static/img/default-cover.svg'">
                    </div>
                </div>

                <div class="wave-capsule">
                    <button id="wave-like-btn" class="wave-capsule-btn ${track.liked ? 'liked' : ''}" title="Нравится">
                        <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                    </button>
                    <span class="wave-capsule-text">${escapeHtml(track.artist || 'Unknown')} — ${escapeHtml(track.title || 'Unknown')}</span>
                    <button id="wave-dislike-btn" class="wave-capsule-btn" title="Не нравится">
                        <i class="fas fa-ban"></i>
                    </button>
                </div>

                <div class="wave-controls">
                    <button id="wave-prev-btn" class="wave-btn-side" title="Предыдущий"><i class="fas fa-backward-step"></i></button>
                    <button id="wave-play-toggle" class="wave-btn-circle" title="Воспроизведение / Пауза">
                        <i class="fas ${isPlaying ? 'fa-pause' : 'fa-play'}" style="${isPlaying ? '' : 'margin-left:4px;'}"></i>
                    </button>
                    <button id="wave-next-btn" class="wave-btn-side" title="Следующий"><i class="fas fa-forward-step"></i></button>
                </div>

                <div class="wave-footer-note">
                    <i class="fas fa-sparkles" style="color: var(--accent);"></i>
                    <span>Поток непрерывно подстраивается под ваши лайки и пропуски</span>
                </div>
            </div>
        `;

        document.getElementById('wave-play-toggle')?.addEventListener('click', togglePlay);
        document.getElementById('wave-next-btn')?.addEventListener('click', () => nextTrack(true));
        document.getElementById('wave-prev-btn')?.addEventListener('click', prevTrack);
        
        document.getElementById('wave-like-btn')?.addEventListener('click', async () => {
            const nextLiked = !track.liked;
            await toggleLike(track.id, track.liked);
            track.liked = nextLiked;
            if (currentlyPlayingTrack) currentlyPlayingTrack.liked = nextLiked;
            if (btnLikePlayer) {
                btnLikePlayer.classList.toggle('liked', nextLiked);
                btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
            }
            renderWaveView();
        });

        document.getElementById('wave-dislike-btn')?.addEventListener('click', () => {
            sendHistory(track.id, 'skip', 1.0);
            nextTrack(false);
        });
    }

    async function initWavePlayback() {
        try {
            const res = await fetch('/wave?limit=20');
            const tracks = await res.json();
            if (tracks.length > 0) {
                playbackQueue.setQueue(tracks);
                await playTrack(0);
                renderWaveView();
            }
        } catch (e) {
            console.error("Ошибка запуска волны:", e);
            showToast("⚠️ Не удалось загрузить волну");
        }
    }

    // =========================================================================
    // Отрисовка экрана поиска (Полки категорий + выдача)
    // =========================================================================
    function renderSearchView() {
        if (!tracklist) return;
        if (viewTitle) viewTitle.textContent = 'Поиск';

        tracklist.innerHTML = `
            <div class="search-view-container">
                <div class="search-input-wrapper">
                    <i class="fas fa-search search-input-icon"></i>
                    <input type="text" id="search-input" class="search-input-field" placeholder="Трек, исполнитель или альбом..." autofocus>
                    <div class="search-chips">
                        <button class="search-chip" data-query="Русский рок">Русский рок</button>
                        <button class="search-chip" data-query="Король и Шут">Король и Шут</button>
                        <button class="search-chip" data-query="Фонк">Фонк</button>
                        <button class="search-chip" data-query="Хиты">Хиты</button>
                    </div>
                </div>

                <div id="search-results-zone">
                    <div class="shelves-container">
                        ${SEARCH_SHELVES.map(shelf => `
                            <div>
                                <h3 class="shelf-title">${shelf.title}</h3>
                                <div class="shelf-grid">
                                    ${shelf.cards.map(card => `
                                        <div class="shelf-card" style="background: ${card.bg};" data-query="${card.name}">
                                            <span class="shelf-card-title">${card.name}</span>
                                            <i class="fas ${card.icon} shelf-card-icon"></i>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
        `;

        const inputEl = document.getElementById('search-input');
        inputEl?.addEventListener('input', (e) => {
            clearTimeout(searchDebounceTimer);
            const query = e.target.value.trim();
            if (!query) {
                renderSearchView();
                return;
            }
            searchDebounceTimer = setTimeout(() => executeSearch(query), 300);
        });

        tracklist.querySelectorAll('.search-chip, .shelf-card').forEach(el => {
            el.addEventListener('click', () => {
                const q = el.dataset.query;
                if (inputEl) inputEl.value = q;
                executeSearch(q);
            });
        });
    }

    async function executeSearch(query) {
        const resultsZone = document.getElementById('search-results-zone');
        if (!resultsZone) return;

        resultsZone.innerHTML = '<div style="padding: 24px; color: var(--text-muted);"><i class="fas fa-spinner fa-spin"></i> Поиск...</div>';

        try {
            const results = await searchTracks(query);
            if (!results || results.length === 0) {
                resultsZone.innerHTML = '<div style="padding: 24px; color: var(--text-muted);">Ничего не найдено</div>';
                return;
            }

            playbackQueue.setQueue(results);

            const groups = results.reduce((acc, track, idx) => {
                let label = 'YouTube Music';
                if (track.source === 'local') label = 'Локально';
                else if (track.source === 'soundcloud') label = 'SoundCloud';

                if (!acc[label]) acc[label] = [];
                acc[label].push({ track, idx });
                return acc;
            }, {});

            resultsZone.innerHTML = '';
            const listWrapper = document.createElement('div');
            listWrapper.className = 'track-list-wrapper';

            for (const [label, items] of Object.entries(groups)) {
                const heading = document.createElement('h4');
                heading.className = 'source-group-heading';
                heading.textContent = label;
                listWrapper.appendChild(heading);

                items.forEach(({ track, idx }) => {
                    listWrapper.appendChild(createTrackRow(track, idx));
                });
            }
            resultsZone.appendChild(listWrapper);
            updateActiveTrackUI();
        } catch {
            resultsZone.innerHTML = '<div style="padding: 24px; color: #ef4444;">Ошибка выполнения поиска</div>';
        }
    }

    // =========================================================================
    // Отрисовка списков (Библиотека / Избранное)
    // =========================================================================
    function createTrackRow(track, index) {
        const row = document.createElement('div');
        const isActive = isSameTrack(currentlyPlayingTrack, track);
        row.className = `track-row ${isActive ? 'active' : ''}`;
        row.dataset.index = index;

        const coverSrc = escapeHtml(track.cover_path || '/static/img/default-cover.svg');
        const durationText = track.duration ? formatTime(track.duration) : '--:--';

        row.innerHTML = `
            <span class="track-row-index">${index + 1}</span>
            <img src="${coverSrc}" alt="Cover" class="track-row-cover" onerror="this.src='/static/img/default-cover.svg'">
            <div class="track-row-titles">
                <span class="track-row-title">${escapeHtml(track.title || 'Unknown Title')}</span>
                <span class="track-row-artist">${escapeHtml(track.artist || 'Unknown Artist')}</span>
            </div>
            <span class="track-row-album">${escapeHtml(track.album || '-')}</span>
            <span class="track-row-duration">${durationText}</span>
            <div class="track-row-actions">
                <button class="track-like-btn ${track.liked ? 'liked' : ''}" data-id="${track.id || ''}">
                    <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                </button>
            </div>
        `;

        row.addEventListener('click', (e) => {
            if (e.target.closest('.track-like-btn')) return;
            playTrack(index);
        });
        return row;
    }

    function renderTracklist(tracks) {
        tracklist.innerHTML = '';
        if (!tracks || tracks.length === 0) {
            tracklist.innerHTML = '<div style="padding: 36px; color: var(--text-muted);">Треки не найдены</div>';
            return;
        }

        playbackQueue.setQueue(tracks);
        const wrapper = document.createElement('div');
        wrapper.className = 'track-list-wrapper';

        tracks.forEach((track, index) => {
            wrapper.appendChild(createTrackRow(track, index));
        });
        tracklist.appendChild(wrapper);
        updateActiveTrackUI();
    }

    function updateActiveTrackUI() {
        if (!currentlyPlayingTrack) return;
        const rows = tracklist.querySelectorAll('.track-row');
        rows.forEach((el, idx) => {
            const track = playbackQueue.queue[idx];
            if (track && isSameTrack(track, currentlyPlayingTrack)) {
                el.classList.add('active');
            } else {
                el.classList.remove('active');
            }
        });
    }

    // =========================================================================
    // Воспроизведение
    // =========================================================================
    async function playTrack(index) {
        if (index < 0 || index >= playbackQueue.length) return;
        clearTimeout(autoSkipTimer);
        const track = playbackQueue.queue[index];

        if (isSameTrack(currentlyPlayingTrack, track) && audioPlayer.src && !audioPlayer.error) {
            if (audioPlayer.paused) {
                try {
                    await audioPlayer.play();
                } catch (e) {
                    console.warn("Play failed:", e);
                }
            } else {
                audioPlayer.pause();
            }
            return;
        }

        playbackQueue.playAt(index);
        currentlyPlayingTrack = track;

        // Регистрация удалённых треков в БД
        if (!track.id || track.id === -1 || track.id === '-1') {
            try {
                const res = await fetch('/tracks/remote', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(track)
                });
                if (res.ok) {
                    const saved = await res.json();
                    track.id = saved.id;
                    currentlyPlayingTrack.id = saved.id;
                } else {
                    showToast("⚠️ Ошибка регистрации внешнего трека");
                    autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                    return;
                }
            } catch {
                showToast("⚠️ Ошибка соединения с сервером");
                autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                return;
            }
        }

        playerTitle.textContent = track.title || 'Unknown Title';
        playerArtist.textContent = track.artist || 'Unknown Artist';
        playerCover.src = track.cover_path || '/static/img/default-cover.svg';
        if (btnLikePlayer) {
            btnLikePlayer.classList.toggle('liked', Boolean(track.liked));
            btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';
        }

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(e => {
            console.warn("Play interrupted or pending:", e);
            syncPlayPauseUI(false);
        });

        sendHistory(track.id, 'play', 0);

        if (currentView === 'wave') {
            renderWaveView();
        } else {
            updateActiveTrackUI();
        }
        prefetchWaveIfNeeded();
    }

    async function togglePlay() {
        if (!currentlyPlayingTrack && playbackQueue.length > 0) {
            playTrack(0);
            return;
        }
        if (audioPlayer.paused) {
            if (audioPlayer.error || !audioPlayer.src || audioPlayer.src.endsWith('/-1')) {
                if (playbackQueue.index >= 0) {
                    playTrack(playbackQueue.index);
                    return;
                }
            }
            try {
                await audioPlayer.play();
            } catch (err) {
                console.warn("audioPlayer.play() failed:", err);
                syncPlayPauseUI(false);
                if (audioPlayer.error) {
                    showToast("⚠️ Ошибка источника. Переключаем...");
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
            if (elapsed < 15) {
                sendHistory(currentlyPlayingTrack.id, 'skip', elapsed);
            }
        }
        const next = playbackQueue.next();
        if (next) {
            playTrack(playbackQueue.index);
        } else if (currentView === 'wave') {
            prefetchWaveIfNeeded().then(() => {
                const retry = playbackQueue.next();
                if (retry) playTrack(playbackQueue.index);
            });
        }
    }

    function prevTrack() {
        clearTimeout(autoSkipTimer);
        const prev = playbackQueue.previous();
        if (prev) {
            playTrack(playbackQueue.index);
        }
    }

    // =========================================================================
    // Слушатели событий плеера
    // =========================================================================
    audioPlayer.addEventListener('play', () => syncPlayPauseUI(true));
    audioPlayer.addEventListener('pause', () => syncPlayPauseUI(false));

    audioPlayer.addEventListener('error', (e) => {
        console.error("Ошибка аудиопотока:", e, audioPlayer.error);
        syncPlayPauseUI(false);
        showToast("⚠️ Трек недоступен. Переключаем...");
        clearTimeout(autoSkipTimer);
        autoSkipTimer = setTimeout(() => nextTrack(false), 1800);
    });

    audioPlayer.addEventListener('timeupdate', () => {
        const cur = audioPlayer.currentTime;
        const dur = audioPlayer.duration || 0;
        if (timeCurrent) timeCurrent.textContent = formatTime(cur);
        if (dur > 0 && progressFill) {
            progressFill.style.width = `${(cur / dur) * 100}%`;
        }
    });

    audioPlayer.addEventListener('loadedmetadata', () => {
        if (timeTotal) timeTotal.textContent = formatTime(audioPlayer.duration || 0);
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

    if (progressTrack) {
        progressTrack.addEventListener('click', (e) => {
            if (!audioPlayer.duration) return;
            const rect = progressTrack.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            audioPlayer.currentTime = pos * audioPlayer.duration;
        });
    }

    if (volumeRange) {
        volumeRange.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            audioPlayer.volume = val / 100;
            audioPlayer.muted = false;
            if (volumeIcon) volumeIcon.className = val === 0 ? 'fas fa-volume-mute' : 'fas fa-volume-up';
        });
    }

    if (btnMute) {
        btnMute.addEventListener('click', () => {
            audioPlayer.muted = !audioPlayer.muted;
            if (volumeIcon) volumeIcon.className = audioPlayer.muted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
        });
    }

    // Лайк из нижнего плеера
    if (btnLikePlayer) {
        btnLikePlayer.addEventListener('click', async () => {
            if (!currentlyPlayingTrack || !currentlyPlayingTrack.id || currentlyPlayingTrack.id === -1) return;
            try {
                const nextLiked = !currentlyPlayingTrack.liked;
                await toggleLike(currentlyPlayingTrack.id, currentlyPlayingTrack.liked);
                currentlyPlayingTrack.liked = nextLiked;
                btnLikePlayer.classList.toggle('liked', nextLiked);
                btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
                if (currentView === 'wave') renderWaveView();
            } catch (err) {
                console.error("Ошибка лайка:", err);
            }
        });
    }

    // Лайк из списков
    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.track-like-btn');
        if (!likeBtn) return;
        e.stopPropagation();
        const trackId = likeBtn.dataset.id;
        if (!trackId || trackId === '-1' || trackId === -1) return;
        const isLiked = likeBtn.classList.contains('liked');
        try {
            await toggleLike(trackId, isLiked);
            const nextLiked = !isLiked;
            likeBtn.classList.toggle('liked', nextLiked);
            likeBtn.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
            if (currentlyPlayingTrack && String(currentlyPlayingTrack.id) === String(trackId)) {
                currentlyPlayingTrack.liked = nextLiked;
                if (btnLikePlayer) {
                    btnLikePlayer.classList.toggle('liked', nextLiked);
                    btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
                }
            }
        } catch (err) {
            console.error("Ошибка лайка:", err);
        }
    });

    // =========================================================================
    // Навигация (Переключение вкладок без прерывания аудио)
    // =========================================================================
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            if (!view || view === currentView) return;

            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            item.classList.add('active');
            currentView = view;

            // Управление видимостью нижнего дока через CSS-класс body
            document.body.className = `view-${view}`;

            if (view === 'wave') {
                renderWaveView();
                if (!currentlyPlayingTrack) {
                    await initWavePlayback();
                }
            } else if (view === 'search') {
                renderSearchView();
            } else if (view === 'library') {
                if (viewTitle) viewTitle.textContent = 'Библиотека';
                try {
                    const tracks = await fetchTracks();
                    renderTracklist(tracks);
                } catch {
                    tracklist.innerHTML = '<div style="padding: 36px; color: #ef4444;">Ошибка загрузки библиотеки</div>';
                }
            } else if (view === 'favorites') {
                if (viewTitle) viewTitle.textContent = 'Любимые треки';
                try {
                    const favs = await fetchFavorites();
                    renderTracklist(favs);
                } catch {
                    tracklist.innerHTML = '<div style="padding: 36px; color: #ef4444;">Ошибка загрузки избранного</div>';
                }
            }
        });
    });

    // Стартовая инициализация
    fetchTracks().then(renderTracklist).catch(() => {
        tracklist.innerHTML = '<div style="padding: 36px; color: var(--text-muted);">Ошибка загрузки медиатеки</div>';
    });
});

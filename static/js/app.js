import PlaybackQueue from './playback_queue.js';

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const tracklist = document.getElementById('tracklist');
    const searchInput = document.getElementById('search-input');
    const viewTitle = document.getElementById('view-title');
    const audioPlayer = document.getElementById('audio-player');
    
    // Player Dock
    const playerTitle = document.getElementById('player-title');
    const playerArtist = document.getElementById('player-artist');
    const playerCover = document.getElementById('player-cover');
    const btnPlay = document.getElementById('btn-play');
    const btnNext = document.getElementById('btn-next');
    const btnPrev = document.getElementById('btn-prev');
    const btnLikePlayer = document.getElementById('btn-like-player');
    const progressFill = document.getElementById('progress-fill');
    const progressContainer = document.querySelector('.progress-bar');
    const timeCurrent = document.getElementById('time-current');
    const timeTotal = document.getElementById('time-total');
    
    const btnMute = document.getElementById('btn-mute');
    const volumeRange = document.getElementById('volume-range');
    const volumeIcon = btnMute ? btnMute.querySelector('i') : null;

    // Lyrics
    const lyricsOverlay = document.getElementById('lyrics-overlay');
    const btnCloseLyrics = document.getElementById('btn-close-lyrics');
    const playerInfo = document.querySelector('.player-info');
    const lyricsCover = document.getElementById('lyrics-cover');
    const lyricsTitle = document.getElementById('lyrics-title');
    const lyricsArtist = document.getElementById('lyrics-artist');
    const lyricsLines = document.getElementById('lyrics-lines');
    const lyricsScrollContainer = document.getElementById('lyrics-scroll-container');
    const lyricsProgressFill = document.getElementById('lyrics-progress-fill');
    const lyricsProgressContainer = document.getElementById('lyrics-progress-container');
    const lyricsTimeCurrent = document.getElementById('lyrics-time-current');
    const lyricsTimeTotal = document.getElementById('lyrics-time-total');

    // State
    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let isPrefetchingWave = false;
    let autoSkipTimer = null;
    let lastSearchResults = [];
    let lastSearchQuery = '';
    try {
        lastSearchQuery = sessionStorage.getItem('lastSearchQuery') || '';
        lastSearchResults = JSON.parse(sessionStorage.getItem('lastSearchResults') || '[]');
    } catch {}
    let parsedLyrics = [];
    let lyricsOffset = 0.0;

    function showToast(msg) {
        let toast = document.getElementById('player-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'player-toast';
            toast.style.cssText = 'position:fixed;bottom:104px;left:50%;transform:translateX(-50%);background:rgba(20,20,20,0.92);color:#ffcc00;padding:10px 22px;border-radius:24px;font-size:14px;font-weight:600;z-index:9999;pointer-events:none;transition:opacity 0.25s ease;border:1px solid #444;box-shadow:0 8px 24px rgba(0,0,0,0.6);';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.style.opacity = '1';
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 2200);
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

    function getTrackOffset(trackId) {
        if (!trackId) return 0.0;
        const saved = localStorage.getItem(`player:offset:${trackId}`);
        return saved !== null ? parseFloat(saved) : 0.0;
    }

    function setTrackOffset(trackId, val) {
        lyricsOffset = Math.round(val * 10) / 10;
        if (trackId && trackId !== -1 && trackId !== '-1') {
            localStorage.setItem(`player:offset:${trackId}`, lyricsOffset);
        }
        updateTimingToolbar();
        updateActiveLyricsLine(audioPlayer.currentTime);
    }

    function isSameTrack(a, b) {
        if (!a || !b) return false;
        if (a.id && b.id && a.id !== -1 && b.id !== -1 && a.id !== '-1' && b.id !== '-1') {
            return a.id === b.id;
        }
        return a.title === b.title && a.artist === b.artist;
    }

    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
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

    // API
    async function fetchTracks(params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const response = await fetch(`/tracks?${queryString}`);
        if (!response.ok) throw new Error('Failed to fetch tracks');
        return await response.json();
    }

    async function fetchFavorites() {
        const response = await fetch('/favorites');
        if (!response.ok) throw new Error('Failed to fetch favorites');
        return await response.json();
    }

    async function searchTracks(query) {
        const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
        if (!response.ok) throw new Error('Search failed');
        return await response.json();
    }

    async function toggleLike(trackId, isLiked) {
        const response = await fetch(`/tracks/${trackId}/like?liked=${!isLiked}`, {
            method: 'POST'
        });
        if (!response.ok) throw new Error('Failed to toggle like');
        return await response.json();
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
                    const extraTracks = await res.json();
                    playbackQueue.appendTracks(extraTracks);
                }
            } catch (err) {
                console.error("Wave prefetch error:", err);
            } finally {
                isPrefetchingWave = false;
            }
        }
    }

    function renderWaveView() {
        if (!tracklist) return;
        if (viewTitle) viewTitle.textContent = '';

        const track = currentlyPlayingTrack || playbackQueue.getCurrentTrack();
        const isPlaying = !audioPlayer.paused && Boolean(currentlyPlayingTrack) && !audioPlayer.error;

        if (!track) {
            tracklist.innerHTML = `
                <div class="wave-screen">
                    <div class="wave-orb-container">
                        <div class="wave-orb"></div>
                        <div class="wave-title-overlay">Моя волна</div>
                    </div>
                    <div class="wave-controls">
                        <button id="btn-wave-start" class="wave-btn-circle" title="Включить волну">
                            <i class="fas fa-play" style="margin-left: 4px;"></i>
                        </button>
                    </div>
                    <div class="wave-subtitle">
                        <i class="fas fa-sparkles" style="color: #ffcc00;"></i>
                        <span>Нажмите, чтобы включить персональный поток</span>
                    </div>
                </div>
            `;
            const startBtn = document.getElementById('btn-wave-start');
            if (startBtn) {
                startBtn.addEventListener('click', async () => {
                    startBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
                    await initWavePlayback();
                });
            }
            return;
        }

        tracklist.innerHTML = `
            <div class="wave-screen ${isPlaying ? 'playing' : ''}">
                <div class="wave-orb-container">
                    <div class="wave-orb"></div>
                    <div class="wave-title-overlay">Моя волна</div>
                </div>

                <div class="wave-capsule">
                    <button id="wave-like-btn" class="wave-btn-secondary" style="font-size: 18px; color: ${track.liked ? '#ff0055' : 'rgba(255,255,255,0.7)'};">
                        <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                    </button>
                    <span class="wave-capsule-text">${escapeHtml(track.artist || 'Unknown')} — ${escapeHtml(track.title || 'Unknown')}</span>
                    <button id="wave-dislike-btn" class="wave-btn-secondary" title="Не нравится" style="font-size: 16px; color: rgba(255,255,255,0.6);">
                        <i class="fas fa-ban"></i>
                    </button>
                </div>

                <div class="wave-controls">
                    <button id="wave-prev-btn" class="wave-btn-secondary" title="Назад">
                        <i class="fas fa-backward"></i>
                    </button>
                    <button id="wave-play-toggle" class="wave-btn-circle" title="Пауза / Играть">
                        <i class="fas ${isPlaying ? 'fa-pause' : 'fa-play'}" style="${isPlaying ? '' : 'margin-left:4px;'}"></i>
                    </button>
                    <button id="wave-next-btn" class="wave-btn-secondary" title="Дальше">
                        <i class="fas fa-forward"></i>
                    </button>
                </div>

                <div class="wave-subtitle">
                    <i class="fas fa-sparkles" style="color: #ffcc00;"></i>
                    <span>Поток непрерывно подстраивается под ваши лайки и пропуски</span>
                </div>
            </div>
        `;

        document.getElementById('wave-play-toggle')?.addEventListener('click', () => {
            togglePlay();
        });
        document.getElementById('wave-next-btn')?.addEventListener('click', () => nextTrack(true));
        document.getElementById('wave-prev-btn')?.addEventListener('click', prevTrack);
        
        document.getElementById('wave-like-btn')?.addEventListener('click', async () => {
            const nextLiked = !track.liked;
            await toggleLike(track.id, track.liked);
            track.liked = nextLiked;
            if (currentlyPlayingTrack) currentlyPlayingTrack.liked = nextLiked;
            if (btnLikePlayer) btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
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
            console.error("Wave launch error:", e);
        }
    }

    // Lyrics
    function parseLRC(text) {
        if (!text) return [];
        const lines = text.split('\n');
        const result = [];
        const timeRegex = /\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]/g;

        lines.forEach(line => {
            const matches = [...line.matchAll(timeRegex)];
            if (matches.length > 0) {
                const textOnly = line.replace(timeRegex, '').trim();
                matches.forEach(match => {
                    const minutes = parseInt(match[1], 10);
                    const seconds = parseInt(match[2], 10);
                    const ms = match[3] ? (match[3].length === 2 ? parseInt(match[3], 10) * 10 : parseInt(match[3], 10)) : 0;
                    const time = minutes * 60 + seconds + ms / 1000;
                    result.push({ time, text: textOnly });
                });
            }
        });

        result.sort((a, b) => a.time - b.time);
        return result;
    }

    async function loadLyrics(track) {
        lyricsOffset = getTrackOffset(track.id);
        updateTimingToolbar();

        if (!lyricsLines) return;
        lyricsLines.innerHTML = '<p class="lyrics-status"><i class="fas fa-spinner fa-spin"></i> Загрузка текста...</p>';
        parsedLyrics = [];

        try {
            let res = await fetch(`/tracks/${track.id}/lyrics`);
            if (!res.ok && track.artist && track.title) {
                res = await fetch(`/lyrics?artist=${encodeURIComponent(track.artist)}&title=${encodeURIComponent(track.title)}&duration=${track.duration || ''}`);
            }
            if (!res.ok) throw new Error('No lyrics');
            const data = await res.json();

            const synced = data.syncedLyrics || (data.lyrics && data.lyrics.includes('[00:') ? data.lyrics : null);
            const plain = data.plainLyrics || data.plain_lyrics || data.lyrics;

            if (synced) {
                parsedLyrics = parseLRC(synced);
                if (parsedLyrics.length > 0) {
                    renderParsedLyrics(parsedLyrics);
                    return;
                }
            }

            if (plain && typeof plain === 'string' && plain.trim().length > 0) {
                renderPlainLyrics(plain);
                return;
            }

            lyricsLines.innerHTML = '<p class="lyrics-status">Текст для этого трека отсутствует</p>';
        } catch {
            lyricsLines.innerHTML = '<p class="lyrics-status">Текст для этого трека отсутствует</p>';
        }
    }

    function renderPlainLyrics(text) {
        if (!lyricsLines) return;
        lyricsLines.innerHTML = '';
        const lines = text.split('\n');
        lines.forEach(line => {
            const p = document.createElement('p');
            p.className = 'lyrics-line plain-line';
            p.textContent = line.trim() || ' ';
            lyricsLines.appendChild(p);
        });
    }

    function renderParsedLyrics(lyrics) {
        if (!lyricsLines) return;
        lyricsLines.innerHTML = '';
        lyrics.forEach((line, idx) => {
            const p = document.createElement('p');
            p.className = 'lyrics-line';
            p.textContent = line.text || '♪';
            p.dataset.index = idx;
            p.dataset.time = line.time;
            p.addEventListener('click', () => {
                audioPlayer.currentTime = Math.max(0, line.time + lyricsOffset);
            });
            lyricsLines.appendChild(p);
        });
    }

    function updateActiveLyricsLine(currentTime) {
        if (!parsedLyrics.length || !lyricsLines) return;
        const adjustedTime = currentTime - lyricsOffset;
        let activeIdx = -1;
        for (let i = 0; i < parsedLyrics.length; i++) {
            if (adjustedTime >= parsedLyrics[i].time) {
                activeIdx = i;
            } else {
                break;
            }
        }

        const lines = lyricsLines.querySelectorAll('.lyrics-line');
        lines.forEach((el, idx) => {
            if (idx === activeIdx) {
                if (!el.classList.contains('active')) {
                    el.classList.add('active');
                    if (lyricsScrollContainer) {
                        const offsetTop = el.offsetTop;
                        const containerHeight = lyricsScrollContainer.clientHeight;
                        lyricsScrollContainer.scrollTo({
                            top: offsetTop - containerHeight / 2 + el.clientHeight / 2,
                            behavior: 'smooth'
                        });
                    }
                }
            } else {
                el.classList.remove('active');
            }
        });
    }

    function updateTimingToolbar() {
        const valEl = document.getElementById('lyrics-timing-val');
        if (valEl) {
            valEl.textContent = (lyricsOffset >= 0 ? '+' : '') + lyricsOffset.toFixed(1) + 's';
        }
    }

    function createLyricsTimingToolbar() {
        if (!lyricsLines || document.getElementById('lyrics-timing-toolbar')) return;

        const bar = document.createElement('div');
        bar.id = 'lyrics-timing-toolbar';
        bar.className = 'lyrics-timing-toolbar';
        bar.innerHTML = `
            <div class="timing-left">
                <button id="btn-sync-now" class="timing-btn sync-btn" title="Нажмите в момент, когда вокалист начинает петь текущую строчку">
                    <i class="fas fa-bullseye"></i> Запели сейчас
                </button>
            </div>
            <div class="timing-controls">
                <span class="timing-title">Сдвиг:</span>
                <button class="timing-btn" data-step="-1.0">-1s</button>
                <button class="timing-btn" data-step="-0.5">-0.5s</button>
                <span id="lyrics-timing-val" class="timing-value">+0.0s</span>
                <button class="timing-btn" data-step="0.5">+0.5s</button>
                <button class="timing-btn" data-step="1.0">+1s</button>
                <button id="btn-sync-reset" class="timing-btn reset-btn" title="Сбросить сдвиг">0s</button>
            </div>
        `;

        lyricsLines.parentNode.insertBefore(bar, lyricsLines);

        bar.addEventListener('click', (e) => {
            const btn = e.target.closest('button');
            if (!btn || !currentlyPlayingTrack) return;

            if (btn.id === 'btn-sync-now') {
                const cur = audioPlayer.currentTime;
                let targetTime = 0;
                if (parsedLyrics.length > 0) {
                    const activeEl = lyricsLines.querySelector('.lyrics-line.active');
                    const idx = activeEl ? parseInt(activeEl.dataset.index, 10) : 0;
                    targetTime = parsedLyrics[idx]?.time || parsedLyrics[0].time;
                }
                const newOffset = Math.max(0, cur - targetTime);
                setTrackOffset(currentlyPlayingTrack.id, newOffset);
            } else if (btn.id === 'btn-sync-reset') {
                setTrackOffset(currentlyPlayingTrack.id, 0.0);
            } else if (btn.dataset.step) {
                const step = parseFloat(btn.dataset.step);
                setTrackOffset(currentlyPlayingTrack.id, lyricsOffset + step);
            }
        });
    }

    // UI Rendering
    function renderTrackItem(track, index) {
        const item = document.createElement('div');
        const isActive = isSameTrack(currentlyPlayingTrack, track);
        item.className = `track-item ${isActive ? 'active' : ''}`;
        item.dataset.index = index;
        if (track.id) item.dataset.id = track.id;
        
        const cover = escapeHtml(track.cover_path || '/static/img/default-cover.svg');
        const duration = track.duration ? formatTime(track.duration) : '--:--';

        item.innerHTML = `
            <div class="track-rank">${index + 1}</div>
            <div class="track-info">
                <img src="${cover}" alt="cover" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/img/default-cover.svg';">
                <div class="track-text">
                    <span class="track-title-name">${escapeHtml(track.title || 'Unknown Title')}</span>
                    <span class="track-artist-name">${escapeHtml(track.artist || 'Unknown Artist')}</span>
                </div>
            </div>
            <div class="track-album">${escapeHtml(track.album || '-')}</div>
            <div class="track-duration">${duration}</div>
            <div class="track-actions">
                <button class="icon-btn like-btn ${track.liked ? 'liked' : ''}" data-id="${track.id || ''}" data-liked="${Boolean(track.liked)}">
                    <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                </button>
            </div>
        `;

        item.addEventListener('click', (e) => {
            if (e.target.closest('.like-btn')) return;
            playTrack(index);
        });
        return item;
    }

    function updateActiveTrackUI() {
        if (!currentlyPlayingTrack) return;
        const items = tracklist.querySelectorAll('.track-item');
        items.forEach((el, idx) => {
            const track = playbackQueue.queue[idx];
            if (track && isSameTrack(track, currentlyPlayingTrack)) {
                el.classList.add('active');
                if (!audioPlayer.paused && !audioPlayer.error) {
                    el.classList.add('playing');
                } else {
                    el.classList.remove('playing');
                }
            } else {
                el.classList.remove('active', 'playing');
            }
        });
    }

    function renderTracklist(tracks) {
        tracklist.innerHTML = '';
        if (!tracks || tracks.length === 0) {
            tracklist.innerHTML = '<div class="status-message">Треки не найдены</div>';
            return;
        }

        playbackQueue.setQueue(tracks);
        tracks.forEach((track, index) => {
            tracklist.appendChild(renderTrackItem(track, index));
        });
        updateActiveTrackUI();
    }

    function renderSearchResults(results) {
        tracklist.innerHTML = '';
        if (!results || results.length === 0) {
            tracklist.innerHTML = '<div class="status-message">Ничего не найдено</div>';
            return;
        }

        playbackQueue.setQueue(results);

        const groups = results.reduce((acc, track, idx) => {
            let label = 'YouTube Music';
            if (track.source === 'local') label = 'Локально на диске';
            else if (track.source === 'soundcloud') label = 'SoundCloud';

            if (!acc[label]) acc[label] = [];
            acc[label].push({ track, idx });
            return acc;
        }, {});

        for (const [label, items] of Object.entries(groups)) {
            const header = document.createElement('div');
            header.className = 'source-group-title';
            header.textContent = label;
            tracklist.appendChild(header);

            items.forEach(({ track, idx }) => {
                tracklist.appendChild(renderTrackItem(track, idx));
            });
        }
        updateActiveTrackUI();
    }

    // Playback Core Logic
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
                    console.error("Failed to register track: HTTP", res.status);
                    showToast("⚠️ Ошибка сервера при сохранении трека");
                    autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                    return;
                }
            } catch (err) {
                console.error("Error registering remote track:", err);
                showToast("⚠️ Ошибка соединения с сервером");
                autoSkipTimer = setTimeout(() => nextTrack(false), 1500);
                return;
            }
        }

        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active', 'playing'));
        const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
        if (activeEl) activeEl.classList.add('active', 'playing');

        playerTitle.textContent = track.title || 'Unknown Title';
        playerArtist.textContent = track.artist || 'Unknown Artist';
        playerCover.src = track.cover_path || '/static/img/default-cover.svg';
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';

        if (lyricsCover) lyricsCover.src = track.cover_path || '/static/img/default-cover.svg';
        if (lyricsTitle) lyricsTitle.textContent = track.title || 'Unknown Title';
        if (lyricsArtist) lyricsArtist.textContent = track.artist || 'Unknown Artist';

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(e => {
            console.warn("Play interrupted or pending stream:", e);
            syncPlayPauseUI(false);
        });

        sendHistory(track.id, 'play', 0);
        loadLyrics(track);

        if (currentView === 'wave') {
            renderWaveView();
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

    function updateMuteIcon(isMuted) {
        if (!volumeIcon) return;
        if (isMuted) {
            volumeIcon.className = 'fas fa-volume-mute';
        } else if (audioPlayer.volume < 0.5) {
            volumeIcon.className = 'fas fa-volume-down';
        } else {
            volumeIcon.className = 'fas fa-volume-up';
        }
    }

    function loadVolumeSettings() {
        const vol = localStorage.getItem('player:volume') ?? 70;
        const muted = localStorage.getItem('player:muted') === 'true';
        if (volumeRange) volumeRange.value = vol;
        audioPlayer.volume = vol / 100;
        audioPlayer.muted = muted;
        updateMuteIcon(muted);
    }

    function saveVolumeSettings() {
        if (!audioPlayer) return;
        localStorage.setItem('player:volume', Math.round(audioPlayer.volume * 100));
        localStorage.setItem('player:muted', audioPlayer.muted ? 'true' : 'false');
    }

    if (volumeRange) {
        volumeRange.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            audioPlayer.volume = val / 100;
            audioPlayer.muted = false;
            updateMuteIcon(false);
            saveVolumeSettings();
        });
    }

    if (btnMute) {
        btnMute.addEventListener('click', () => {
            audioPlayer.muted = !audioPlayer.muted;
            updateMuteIcon(audioPlayer.muted);
            saveVolumeSettings();
        });
    }

    if (progressContainer) {
        progressContainer.addEventListener('click', (e) => {
            if (!audioPlayer.duration) return;
            const rect = progressContainer.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            audioPlayer.currentTime = pos * audioPlayer.duration;
        });
    }

    if (lyricsProgressContainer) {
        lyricsProgressContainer.addEventListener('click', (e) => {
            if (!audioPlayer.duration) return;
            const rect = lyricsProgressContainer.getBoundingClientRect();
            const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            audioPlayer.currentTime = pos * audioPlayer.duration;
        });
    }

    audioPlayer.addEventListener('timeupdate', () => {
        const cur = audioPlayer.currentTime;
        const dur = audioPlayer.duration || 0;
        if (timeCurrent) timeCurrent.textContent = formatTime(cur);
        if (lyricsTimeCurrent) lyricsTimeCurrent.textContent = formatTime(cur);
        if (dur > 0) {
            const percent = (cur / dur) * 100;
            if (progressFill) progressFill.style.width = `${percent}%`;
            if (lyricsProgressFill) lyricsProgressFill.style.width = `${percent}%`;
        }
        updateActiveLyricsLine(cur);
    });

    audioPlayer.addEventListener('loadedmetadata', () => {
        const dur = audioPlayer.duration || 0;
        if (timeTotal) timeTotal.textContent = formatTime(dur);
        if (lyricsTimeTotal) lyricsTimeTotal.textContent = formatTime(dur);
    });

    audioPlayer.addEventListener('play', () => {
        syncPlayPauseUI(true);
    });

    audioPlayer.addEventListener('pause', () => {
        syncPlayPauseUI(false);
    });

    audioPlayer.addEventListener('error', (e) => {
        console.error("Audio playback error:", e, audioPlayer.error);
        syncPlayPauseUI(false);
        showToast("⚠️ Трек недоступен. Переключаем на следующий...");
        clearTimeout(autoSkipTimer);
        autoSkipTimer = setTimeout(() => {
            nextTrack(false);
        }, 1800);
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

    if (btnLikePlayer) {
        btnLikePlayer.addEventListener('click', async () => {
            if (!currentlyPlayingTrack || !currentlyPlayingTrack.id || currentlyPlayingTrack.id === -1 || currentlyPlayingTrack.id === '-1') return;
            try {
                const nextLiked = !currentlyPlayingTrack.liked;
                await toggleLike(currentlyPlayingTrack.id, currentlyPlayingTrack.liked);
                currentlyPlayingTrack.liked = nextLiked;
                btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
                const itemBtn = tracklist.querySelector(`.like-btn[data-id="${currentlyPlayingTrack.id}"]`);
                if (itemBtn) {
                    itemBtn.dataset.liked = nextLiked;
                    itemBtn.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
                }
                if (currentView === 'wave') renderWaveView();
            } catch (err) {
                console.error('Failed to toggle like:', err);
            }
        });
    }

    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.like-btn');
        if (!likeBtn) return;
        e.stopPropagation();
        e.preventDefault();
        const trackId = likeBtn.dataset.id;
        if (!trackId || trackId === '-1' || trackId === -1) return;
        const isLiked = likeBtn.dataset.liked === 'true' || likeBtn.dataset.liked === '1';
        try {
            await toggleLike(trackId, isLiked);
            const nextLiked = !isLiked;
            likeBtn.dataset.liked = nextLiked;
            likeBtn.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
            if (currentlyPlayingTrack && String(currentlyPlayingTrack.id) === String(trackId)) {
                currentlyPlayingTrack.liked = nextLiked;
                if (btnLikePlayer) {
                    btnLikePlayer.querySelector('i').className = nextLiked ? 'fas fa-heart' : 'far fa-heart';
                }
            }
        } catch (err) {
            console.error('Failed to toggle like:', err);
        }
    });

    if (playerInfo) {
        playerInfo.addEventListener('click', (e) => {
            if (e.target.closest('#btn-like-player')) return;
            if (lyricsOverlay) lyricsOverlay.classList.remove('hidden');
        });
    }

    if (btnCloseLyrics) {
        btnCloseLyrics.addEventListener('click', () => {
            if (lyricsOverlay) lyricsOverlay.classList.add('hidden');
        });
    }

    // Навигация
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            if (!view || view === currentView) return;

            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            item.classList.add('active');
            currentView = view;

            if (view === 'wave') {
                renderWaveView();
                if (!currentlyPlayingTrack) {
                    await initWavePlayback();
                }
            } else if (view === 'library') {
                if (viewTitle) viewTitle.textContent = 'Your Library';
                try {
                    const tracks = await fetchTracks();
                    renderTracklist(tracks);
                } catch {
                    tracklist.innerHTML = '<div class="status-message">Ошибка загрузки библиотеки</div>';
                }
            } else if (view === 'favorites') {
                if (viewTitle) viewTitle.textContent = 'Любимые треки';
                try {
                    const favs = await fetchFavorites();
                    renderTracklist(favs);
                } catch {
                    tracklist.innerHTML = '<div class="status-message">Ошибка загрузки избранного</div>';
                }
            } else if (view === 'search') {
                const query = lastSearchQuery || (searchInput ? searchInput.value.trim() : '');
                if (query) {
                    if (viewTitle) viewTitle.textContent = `Результаты: "${query}"`;
                    if (searchInput) searchInput.value = query;
                    if (lastSearchResults && lastSearchResults.length > 0) {
                        renderSearchResults(lastSearchResults);
                    } else {
                        tracklist.innerHTML = '<div class="status-message"><i class="fas fa-spinner fa-spin"></i> Поиск...</div>';
                        try {
                            const res = await searchTracks(query);
                            lastSearchResults = res;
                            sessionStorage.setItem('lastSearchResults', JSON.stringify(res));
                            renderSearchResults(res);
                        } catch {
                            tracklist.innerHTML = '<div class="status-message">Ошибка поиска</div>';
                        }
                    }
                } else {
                    if (viewTitle) viewTitle.textContent = 'Search';
                    tracklist.innerHTML = '<div class="status-message">Введите запрос в строку поиска</div>';
                }
                if (searchInput) searchInput.focus();
            }
        });
    });

    if (searchInput) {
        if (lastSearchQuery && !searchInput.value) {
            searchInput.value = lastSearchQuery;
        }
        searchInput.addEventListener('input', debounce(async (e) => {
            const query = e.target.value.trim();
            lastSearchQuery = query;
            sessionStorage.setItem('lastSearchQuery', query);

            if (!query) {
                lastSearchResults = [];
                sessionStorage.removeItem('lastSearchResults');
                if (currentView === 'search') {
                    if (viewTitle) viewTitle.textContent = 'Search';
                    tracklist.innerHTML = '<div class="status-message">Введите запрос в строку поиска</div>';
                }
                return;
            }
            try {
                const results = await searchTracks(query);
                lastSearchResults = results;
                sessionStorage.setItem('lastSearchResults', JSON.stringify(results));
                document.querySelectorAll('.nav-item').forEach(el => {
                    el.classList.toggle('active', el.dataset.view === 'search');
                });
                currentView = 'search';
                if (viewTitle) viewTitle.textContent = `Результаты: "${query}"`;
                renderSearchResults(results);
            } catch (err) {
                console.error('Search error:', err);
            }
        }, 300));
    }

    createLyricsTimingToolbar();
    loadVolumeSettings();
    fetchTracks().then(renderTracklist).catch(() => {
        tracklist.innerHTML = '<div class="status-message">Ошибка загрузки треков</div>';
    });
});

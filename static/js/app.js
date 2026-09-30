import PlaybackQueue from './playback_queue.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const tracklist = document.getElementById('tracklist');
    const searchInput = document.getElementById('search-input');
    const viewTitle = document.getElementById('view-title');
    const audioPlayer = document.getElementById('audio-player');
    
    // Player Dock Elements
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

    // Lyrics Elements
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

    // --- State ---
    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let lastSearchResults = [];
    let lastSearchQuery = '';
    let parsedLyrics = [];

    // --- Utilities ---
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

    // --- API Methods ---
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

    // --- Lyrics Logic ---
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
            if (data && (data.lyrics || data.plain_lyrics)) {
                parsedLyrics = parseLRC(data.lyrics || data.plain_lyrics);
                if (parsedLyrics.length > 0) {
                    renderParsedLyrics(parsedLyrics);
                    return;
                }
            }
            lyricsLines.innerHTML = '<p class="lyrics-status">Текст для этого трека отсутствует</p>';
        } catch {
            lyricsLines.innerHTML = '<p class="lyrics-status">Текст для этого трека отсутствует</p>';
        }
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
                audioPlayer.currentTime = line.time;
            });
            lyricsLines.appendChild(p);
        });
    }

    function updateActiveLyricsLine(currentTime) {
        if (!parsedLyrics.length || !lyricsLines) return;
        let activeIdx = -1;
        for (let i = 0; i < parsedLyrics.length; i++) {
            if (currentTime >= parsedLyrics[i].time) {
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

    // --- UI Rendering ---
    function renderTrackItem(track, index) {
        const item = document.createElement('div');
        const isActive = isSameTrack(currentlyPlayingTrack, track);
        item.className = `track-item ${isActive ? 'active' : ''}`;
        item.dataset.index = index;
        if (track.id) item.dataset.id = track.id;
        
        const cover = track.cover_path || '/static/img/default-cover.png';
        const duration = track.duration ? formatTime(track.duration) : '--:--';

        item.innerHTML = `
            <div class="track-rank">${index + 1}</div>
            <div class="track-info">
                <img src="${cover}" alt="cover">
                <div class="track-text">
                    <span class="track-title-name">${track.title || 'Unknown Title'}</span>
                    <span class="track-artist-name">${track.artist || 'Unknown Artist'}</span>
                </div>
            </div>
            <div class="track-album">${track.album || '-'}</div>
            <div class="track-duration">${duration}</div>
            <div class="track-actions">
                <button class="icon-btn like-btn" data-id="${track.id || ''}" data-liked="${track.liked}">
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
                if (!audioPlayer.paused) {
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
            const label = track.source === 'local' ? 'Локально' : 'Найдено онлайн';
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

    // --- Player Logic ---
    async function playTrack(index) {
        if (index < 0 || index >= playbackQueue.length) return;
        const track = playbackQueue.queue[index];

        if (isSameTrack(currentlyPlayingTrack, track) && audioPlayer.src) {
            if (audioPlayer.paused) {  
                audioPlayer.play();  
                btnPlay.querySelector('i').className = 'fas fa-pause-circle';
            } else {  
                audioPlayer.pause();  
                btnPlay.querySelector('i').className = 'fas fa-play-circle';
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
                    const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
                    if (activeEl) {
                        activeEl.dataset.id = saved.id;
                        const likeBtn = activeEl.querySelector('.like-btn');
                        if (likeBtn) likeBtn.dataset.id = saved.id;
                    }
                }
            } catch (err) {
                console.error("Error registering remote track:", err);
            }
        }

        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active', 'playing'));
        const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
        if (activeEl) activeEl.classList.add('active', 'playing');

        playerTitle.textContent = track.title || 'Unknown Title';
        playerArtist.textContent = track.artist || 'Unknown Artist';
        playerCover.src = track.cover_path || '/static/img/default-cover.png';
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';

        if (lyricsCover) lyricsCover.src = track.cover_path || '/static/img/default-cover.png';
        if (lyricsTitle) lyricsTitle.textContent = track.title || 'Unknown Title';
        if (lyricsArtist) lyricsArtist.textContent = track.artist || 'Unknown Artist';

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(e => console.log("Play interrupted:", e));
        btnPlay.querySelector('i').className = 'fas fa-pause-circle';

        sendHistory(track.id, 'play', 0);
        loadLyrics(track);
    }

    function togglePlay() {
        if (!currentlyPlayingTrack && playbackQueue.length > 0) {
            playTrack(0);
            return;
        }
        if (audioPlayer.paused) {
            audioPlayer.play();
            btnPlay.querySelector('i').className = 'fas fa-pause-circle';
        } else {
            audioPlayer.pause();
            btnPlay.querySelector('i').className = 'fas fa-play-circle';
        }
    }

    function nextTrack(isSkip = false) {
        if (isSkip && currentlyPlayingTrack) {
            sendHistory(currentlyPlayingTrack.id, 'skip', audioPlayer.currentTime || 0);
        }
        const next = playbackQueue.next();
        if (next) {
            playTrack(playbackQueue.index);
        }
    }

    function prevTrack() {
        const prev = playbackQueue.previous();
        if (prev) {
            playTrack(playbackQueue.index);
        }
    }

    // --- Volume & Mute Logic ---
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

    // --- Controls Listeners ---
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
        if (btnPlay) btnPlay.querySelector('i').className = 'fas fa-pause-circle';
        updateActiveTrackUI();
    });

    audioPlayer.addEventListener('pause', () => {
        if (btnPlay) btnPlay.querySelector('i').className = 'fas fa-play-circle';
        updateActiveTrackUI();
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

    // --- Like Buttons ---
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
            } catch (err) {
                console.error('Failed to toggle like:', err);
            }
        });
    }

    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.like-btn');
        if (!likeBtn) return;
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

    // --- Lyrics Overlay Toggle ---
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

    // --- Navigation Tabs ---
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            if (!view) return;
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            item.classList.add('active');
            currentView = view;

            if (view === 'library') {
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
                if (viewTitle) viewTitle.textContent = 'Search';
                if (lastSearchResults.length > 0) {
                    renderSearchResults(lastSearchResults);
                } else {
                    tracklist.innerHTML = '<div class="status-message">Введите запрос в строку поиска</div>';
                }
                if (searchInput) searchInput.focus();
            }
        });
    });

    // --- Search Input ---
    if (searchInput) {
        searchInput.addEventListener('input', debounce(async (e) => {
            const query = e.target.value.trim();
            if (!query) {
                if (currentView === 'search') {
                    tracklist.innerHTML = '<div class="status-message">Введите запрос в строку поиска</div>';
                }
                return;
            }
            lastSearchQuery = query;
            try {
                const results = await searchTracks(query);
                lastSearchResults = results;
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

    // --- Initialization ---
    loadVolumeSettings();
    fetchTracks().then(renderTracklist).catch(() => {
        tracklist.innerHTML = '<div class="status-message">Ошибка загрузки треков</div>';
    });
});

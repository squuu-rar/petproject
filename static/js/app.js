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
    let isDragging = false;
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

    async function loadLyrics(trackId) {
        if (!lyricsLines) return;
        lyricsLines.innerHTML = '<p class="lyrics-status"><i class="fas fa-spinner fa-spin"></i> Загрузка текста...</p>';
        parsedLyrics = [];

        try {
            const response = await fetch(`/tracks/${trackId}/lyrics`);
            if (!response.ok) throw new Error('No lyrics');
            const data = await response.json();
            if (data.lyrics) {
                parsedLyrics = parseLRC(data.lyrics);
                if (parsedLyrics.length > 0) {
                    renderParsedLyrics(parsedLyrics);
                    return;
                }
            }
            lyricsLines.innerHTML = '<p class="lyrics-status">Текст для этого трека отсутствует</p>';
        } catch (err) {
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
            tracklist.innerHTML = '<div class="status-message">No tracks found</div>';
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
            const label = track.source === 'local' ? 'Локально' : 'Найдено';
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

        if (track.id && track.id !== -1 && track.id !== '-1') {
            loadLyrics(track.id);
        }
    }

    function togglePlay() {
        if (audioPlayer.paused) {
            audioPlayer.play();
            btnPlay.querySelector('i').className = 'fas fa-pause-circle';
        } else {
            audioPlayer.pause();
            btnPlay.querySelector('i').className = 'fas fa-play-circle';
        }
    }

    function nextTrack() {
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

    function saveVolumeSettings()

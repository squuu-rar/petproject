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
    const btnLikePlayer = document.getElementById('btn-like-player');
    const progressFill = document.getElementById('progress-fill');
    const progressContainer = document.querySelector('.progress-bar');
    const timeCurrent = document.getElementById('time-current');
    const timeTotal = document.getElementById('time-total');
    
    const btnMute = document.getElementById('btn-mute');
    const volumeRange = document.getElementById('volume-range');
    const volumeIcon = btnMute.querySelector('i');

    // --- State ---
    const playbackQueue = new PlaybackQueue();
    let currentlyPlayingTrack = null;
    let currentView = 'library';
    let isDragging = false;
    let lastSearchResults = [];
    let lastSearchQuery = '';

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
                }
            } else {
                el.classList.remove('active', 'playing');
            }
        });
    }

    function renderTracklist(tracks) {
        tracklist.innerHTML = '';
        if (tracks.length === 0) {
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
        if (results.length === 0) {
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

        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active'));
        const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
        if (activeEl) activeEl.classList.add('active');

        playerTitle.textContent = track.title || 'Unknown Title';
        playerArtist.textContent = track.artist || 'Unknown Artist';
        playerCover.src = track.cover_path || '/static/img/default-cover.png';
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';

        audioPlayer.src = `/stream/${track.id}`;
        audioPlayer.play().catch(e => console.log("Play interrupted:", e));
        btnPlay.querySelector('i').className = 'fas fa-pause-circle';
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
        
        volumeRange.value = vol;
        audioPlayer.volume = vol / 100;
        audioPlayer.muted = muted;
        updateMuteIcon(muted);
    }

    function saveVolumeSettings() {
        localStorage.setItem('player:volume', volumeRange.value);
        localStorage.setItem('player:muted', audioPlayer.muted);
    }

    // --- Event Listeners ---

    const handleSearch = debounce(async (query) => {
        lastSearchQuery = query;
        if (!query) {
            lastSearchResults = [];
            viewTitle.textContent = "Your Library";
            currentView = 'library';
            document.querySelectorAll('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.view === 'library'));
            fetchTracks().then(tracks => {
                renderTracklist(tracks);
                currentlyPlayingTrack = playbackQueue.getCurrentTrack();
            });
            return;
        }

        currentView = 'search';
        document.querySelectorAll('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.view === 'search'));

        viewTitle.textContent = `Results for "${query}"`;
        tracklist.innerHTML = '<div class="status-message"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
        
        try {
            const results = await searchTracks(query);
            lastSearchResults = results;
            renderSearchResults(results);
            currentlyPlayingTrack = playbackQueue.getCurrentTrack();
        } catch (err) {
            console.error(err);
            tracklist.innerHTML = '<div class="status-message">Search error occurred</div>';
        }
    }, 300);

    searchInput.addEventListener('input', (e) => {
        handleSearch(e.target.value.trim());
    });

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            
            currentView = item.dataset.view;
            viewTitle.textContent = item.querySelector('span').textContent;
            
            if (currentView === 'library') {
                fetchTracks().then(tracks => {
                    renderTracklist(tracks);
                    currentlyPlayingTrack = playbackQueue.getCurrentTrack();
                });
            } else if (currentView === 'liked') {
                fetchTracks({source: 'local'}).then(tracks => {
                    const likedTracks = tracks.filter(t => t.liked);
                    renderTracklist(likedTracks);
                    currentlyPlayingTrack = playbackQueue.getCurrentTrack();
                });
            } else if (currentView === 'search') {
                if (lastSearchResults.length > 0) {
                    viewTitle.textContent = `Results for "${lastSearchQuery}"`;
                    searchInput.value = lastSearchQuery;
                    renderSearchResults(lastSearchResults);
                    currentlyPlayingTrack = playbackQueue.getCurrentTrack();
                } else {
                    viewTitle.textContent = "Search";
                    tracklist.innerHTML = '<div class="status-message">Введите запрос в строку поиска выше</div>';
                    searchInput.focus();
                }
            }
        });
    });

    btnPlay.addEventListener('click', togglePlay);
    btnNext.addEventListener('click', nextTrack);
    audioPlayer.addEventListener('ended', nextTrack);
    btnPrev.addEventListener('click', prevTrack);

    // --- Optimistic Like Logic ---

    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.like-btn');
        if (!likeBtn || !likeBtn.dataset.id) return;

        const trackId = parseInt(likeBtn.dataset.id);
        const wasLiked = likeBtn.dataset.liked === 'true';
        const newLiked = !wasLiked;

        // Optimistic Update
        likeBtn.dataset.liked = newLiked;
        likeBtn.querySelector('i').className = newLiked ? 'fas fa-heart' : 'far fa-heart';

        try {
            await toggleLike(trackId, wasLiked);
            
            // Reconcile state
            const track = playbackQueue.queue.find(t => t.id === trackId);
            if (track) track.liked = newLiked;
            
            if (currentlyPlayingTrack && currentlyPlayingTrack.id === trackId) {
                currentlyPlayingTrack.liked = newLiked;
                btnLikePlayer.querySelector('i').className = newLiked ? 'fas fa-heart' : 'far fa-heart';
            }
        } catch (err) {
            // Rollback
            likeBtn.dataset.liked = wasLiked;
            likeBtn.querySelector('i').className = wasLiked ? 'fas fa-heart' : 'far fa-heart';
        }
    });

    btnLikePlayer.addEventListener('click', async (e) => {
        if (e) e.stopPropagation();
        const track = currentlyPlayingTrack || (playbackQueue.index !== -1 ? playbackQueue.queue[playbackQueue.index] : null);
        if (!track || !track.id || track.id === -1 || track.id === '-1') return;

        const wasLiked = track.liked;
        const newLiked = !wasLiked;

        // Optimistic Update
        track.liked = newLiked;
        btnLikePlayer.querySelector('i').className = newLiked ? 'fas fa-heart' : 'far fa-heart';

        try {
            await toggleLike(track.id, wasLiked);
        } catch (err) {
            // Rollback
            track.liked = wasLiked;
            btnLikePlayer.querySelector('i').className = wasLiked ? 'fas fa-heart' : 'far fa-heart';
        }

        // Sync tracklist item
        const activeItem = tracklist.querySelector(`.track-item[data-id="${track.id}"]`);
        if (activeItem) {
            const likeBtn = activeItem.querySelector('.like-btn');
            if (likeBtn) {
                likeBtn.dataset.liked = track.liked;
                likeBtn.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';
            }
        }
    });

    // --- Progress Bar Logic ---

    function updateProgress() {
        if (audioPlayer.duration) {
            const percent = (audioPlayer.currentTime / audioPlayer.duration) * 100;
            progressFill.style.width = `${percent}%`;
            timeCurrent.textContent = formatTime(audioPlayer.currentTime);
            timeTotal.textContent = formatTime(audioPlayer.duration);
        }
    }

    function seek(e) {
        const rect = progressContainer.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        const newTime = pos * audioPlayer.duration;
        audioPlayer.currentTime = newTime;
    }

    audioPlayer.addEventListener('timeupdate', updateProgress);

    progressContainer.addEventListener('click', (e) => {
        seek(e);
    });

    progressContainer.addEventListener('mousedown', (e) => {
        isDragging = true;
        seek(e);
    });

    window.addEventListener('mousemove', (e) => {
        if (isDragging) {
            seek(e);
        }
    });

    window.addEventListener('mouseup', () => {
        isDragging = false;
    });

    // --- Volume & Mute Logic ---

    function updateMuteIcon(isMuted) {
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
        
        volumeRange.value = vol;
        audioPlayer.volume = vol / 100;
        audioPlayer.muted = muted;
        updateMuteIcon(muted);
    }

    function saveVolumeSettings() {
        localStorage.setItem('player:volume', volumeRange.value);
        localStorage.setItem('player:muted', audioPlayer.muted);
    }

    volumeRange.addEventListener('input', () => {
        audioPlayer.volume = volumeRange.value / 100;
        audioPlayer.muted = false;
        updateMuteIcon(audioPlayer.muted);
        saveVolumeSettings();
    });

    btnMute.addEventListener('click', () => {
        audioPlayer.muted = !audioPlayer.muted;
        updateMuteIcon(audioPlayer.muted);
        saveVolumeSettings();
    });

    // --- Initialization ---
    async function init() {
        try {
            const tracks = await fetchTracks();
            renderTracklist(tracks);
            loadVolumeSettings();
        } catch (err) {
            console.error(err);
            tracklist.innerHTML = '<div class="status-message">Failed to load library.</div>';
        }
    }

    init();
});

function setupPlayingSync() {
    const audioEl = document.querySelector('audio');
    if (!audioEl) return;

    audioEl.addEventListener('play', () => {
        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('playing'));
        const active = document.querySelector('.track-item.active');
        if (active) active.classList.add('playing');

        const playBtnIcon = document.querySelector('#btn-play i');
        if (playBtnIcon) {
            playBtnIcon.className = 'fas fa-pause-circle';
        }
    });

    audioEl.addEventListener('pause', () => {
        document.querySelectorAll('.track-item.playing').forEach(el => el.classList.remove('playing'));
        const playBtnIcon = document.querySelector('#btn-play i');
        if (playBtnIcon) {
            playBtnIcon.className = 'fas fa-play-circle';
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPlayingSync);
} else {
    setupPlayingSync();
}

// --- Synced Lyrics Overlay Module ---
document.addEventListener('DOMContentLoaded', () => {
    const overlay = document.getElementById('lyrics-overlay');
    const btnClose = document.getElementById('btn-close-lyrics');
    const coverDock = document.getElementById('player-cover');
    const bigCover = document.getElementById('lyrics-cover');
    const bigTitle = document.getElementById('lyrics-title');
    const bigArtist = document.getElementById('lyrics-artist');
    const lyricsLinesBox = document.getElementById('lyrics-lines');
    const bigProgressFill = document.getElementById('lyrics-progress-fill');
    const lyricsProgressContainer = document.getElementById('lyrics-progress-container');
    const lyricsTimeCurrent = document.getElementById('lyrics-time-current');
    const lyricsTimeTotal = document.getElementById('lyrics-time-total');
    const lyricsScrollContainer = document.getElementById('lyrics-scroll-container');

    btnClose.addEventListener('click', () => {
        overlay.classList.add('hidden');
    });

    // Implementation of lyrics logic would go here
});

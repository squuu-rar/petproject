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
    const btnPrev = document.getElementById('btn-prev');
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
    let currentTracks = [];
    let currentIndex = -1;
    let currentTrack = null;
    let currentView = 'library';

    // --- Utilities ---

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
        if (!seconds) return '--:--';
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
        return await response.json();
    }

    // --- UI Rendering ---

    function renderTrackItem(track, index) {
        const item = document.createElement('div');
        item.className = `track-item ${(currentTrack && currentTrack.id === track.id) ? "active" : ""}`;
        item.dataset.index = index;
        item.dataset.id = track.id;
        
        const cover = track.cover_path || '/static/img/default-cover.svg';
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
                <button class="icon-btn like-btn" data-id="${track.id}" data-liked="${track.liked}">
                    <i class="${track.liked ? 'fas' : 'far'} fa-heart"></i>
                </button>
            </div>
        `;

        item.addEventListener('click', () => playTrack(index));
        return item;
    }

    function renderTracklist(tracks) {
        tracklist.innerHTML = '';
        if (tracks.length === 0) {
            tracklist.innerHTML = '<div class="status-message">No tracks found</div>';
            return;
        }

        tracks.forEach((track, index) => {
            tracklist.appendChild(renderTrackItem(track, index));
        });
    }

    function renderSearchResults(results) {
        tracklist.innerHTML = '';
        if (results.length === 0) {
            tracklist.innerHTML = '<div class="status-message">Ничего не найдено</div>';
            return;
        }

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
    }

    // --- Player Logic ---

    async function playTrack(index) {
        if (index < 0 || index >= currentTracks.length) return;
        const target = currentTracks[index];

        if (currentTrack && currentTrack.id === target.id) {
            if (audioPlayer.paused) {
                audioPlayer.play();
                updatePlayBtn(true);
            } else {
                audioPlayer.pause();
                updatePlayBtn(false);
            }
            return;
        }

        currentIndex = index;
        currentTrack = target;
        
        // Обновляем плеер-док
        const dockTitle = document.querySelector('.track-title-name, .dock-title');
        const dockArtist = document.querySelector('.track-artist-name, .dock-artist');
        const dockCover = document.querySelector('.player-dock img, .dock-cover');
        if (dockTitle) dockTitle.textContent = target.title || 'Unknown Title';
        if (dockArtist) dockArtist.textContent = target.artist || 'Unknown Artist';
        if (dockCover) dockCover.src = target.cover_path || '/static/img/default-cover.svg';

        audioPlayer.src = target.stream_url || `/stream/${target.id}`;
        audioPlayer.play().then(() => {
            updatePlayBtn(true);
        }).catch(err => console.error("Playback error:", err));

        // Снимаем класс active со всех и добавляем текущему
        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active'));
        const activeEl = document.querySelector(`.track-item[data-id="${target.id}"], .track-item[data-index="${index}"]`);
        if (activeEl) activeEl.classList.add('active');
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
        if (currentIndex < currentTracks.length - 1) {
            playTrack(currentIndex + 1);
        } else {
            playTrack(0);
        }
    }

    function prevTrack() {
        if (currentIndex > 0) {
            playTrack(currentIndex - 1);
        } else {
            playTrack(0);
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
        if (!query) {
            viewTitle.textContent = "Your Library";
            currentView = 'library';
            fetchTracks().then(tracks => {
                currentTracks = tracks;
                renderTracklist(tracks);
            });
            return;
        }

        viewTitle.textContent = `Results for "${query}"`;
        tracklist.innerHTML = '<div class="status-message"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
        
        try {
            const results = await searchTracks(query);
            currentTracks = results;
            renderSearchResults(results);
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
                    currentTracks = tracks;
                    renderTracklist(tracks);
                });
            } else if (currentView === 'liked') {
                fetchTracks({source: 'local'}).then(tracks => {
                    currentTracks = tracks.filter(t => t.liked);
                    renderTracklist(currentTracks);
                });
            }
        });
    });

    btnPlay.addEventListener('click', togglePlay);
    btnNext.addEventListener('click', nextTrack);
    btnPrev.addEventListener('click', prevTrack);

    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.like-btn');
        if (!likeBtn) return;

        const trackId = parseInt(likeBtn.dataset.id);
        const isLiked = likeBtn.dataset.liked === 'true';
        
        await toggleLike(trackId, isLiked);
        
        likeBtn.dataset.liked = !isLiked;
        likeBtn.querySelector('i').className = !isLiked ? 'fas fa-heart' : 'far fa-heart';
        
        if (currentIndex !== -1 && currentTracks[currentIndex].id === trackId) {
            btnLikePlayer.querySelector('i').className = !isLiked ? 'fas fa-heart' : 'far fa-heart';
        }
    });

    btnLikePlayer.addEventListener('click', async () => {
        if (currentIndex === -1) return;
        const track = currentTracks[currentIndex];
        await toggleLike(track.id, track.liked);
        track.liked = !track.liked;
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';
    });

    audioPlayer.addEventListener('timeupdate', () => {
        if (audioPlayer.duration) {
            const percent = (audioPlayer.currentTime / audioPlayer.duration) * 100;
            progressFill.style.width = `${percent}%`;
            timeCurrent.textContent = formatTime(audioPlayer.currentTime);
            timeTotal.textContent = formatTime(audioPlayer.duration);
        }
    });

    progressContainer.addEventListener('click', (e) => {
        const rect = progressContainer.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        audioPlayer.currentTime = pos * audioPlayer.duration;
    });

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
            currentTracks = tracks;
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

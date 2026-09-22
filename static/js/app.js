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
    const volumeFill = document.getElementById('volume-fill');
    const volumeSlider = document.querySelector('.volume-slider');

    // --- State ---
    let currentTracks = [];
    let currentIndex = -1;
    let currentView = 'library';

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

    function renderTracklist(tracks) {
        tracklist.innerHTML = '';
        if (tracks.length === 0) {
            tracklist.innerHTML = '<div class="empty-state">No tracks found</div>';
            return;
        }

        tracks.forEach((track, index) => {
            const item = document.createElement('div');
            item.className = `track-item ${currentIndex === index ? 'active' : ''}`;
            item.dataset.index = index;
            
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
            tracklist.appendChild(item);
        });
    }

    function formatTime(seconds) {
        if (!seconds) return '--:--';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    // --- Player Logic ---

    async function playTrack(index) {
        const _audio = document.querySelector('audio');
        if (currentIndex === index && _audio) {
            if (_audio.paused) { _audio.play(); } else { _audio.pause(); }
            return;
        }
        if (index < 0 || index >= currentTracks.length) return;

        currentIndex = index;
        const track = currentTracks[index];

        // Если трек пришел из внешнего поиска без id, сохраняем его в SQLite
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
                    const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
                    if (activeEl) {
                        const likeBtn = activeEl.querySelector('.like-btn');
                        if (likeBtn) likeBtn.dataset.id = saved.id;
                    }
                }
            } catch (err) {
                console.error("Не удалось зарегистрировать remote трек:", err);
            }
        }

        // Update UI
        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active'));
        const activeEl = tracklist.querySelector(`[data-index="${index}"]`);
        if (activeEl) activeEl.classList.add('active');

        playerTitle.textContent = track.title || 'Unknown Title';
        playerArtist.textContent = track.artist || 'Unknown Artist';
        playerCover.src = track.cover_path || '/static/img/default-cover.svg';
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';

        // Audio Source
        const streamUrl = `/stream/${track.id}`;
        audioPlayer.src = streamUrl;
        audioPlayer.play().catch(e => console.log("Play interrupted or loading:", e));
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

    // --- Event Listeners ---

    // Search
    searchInput.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter') {
            const query = e.target.value.trim();
            if (query) {
                viewTitle.textContent = `Results for "${query}"`;
                const results = await searchTracks(query);
                currentTracks = results;
                renderTracklist(currentTracks);
            }
        }
    });

    // Navigation
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
                // For simplicity, we just filter local tracks that are liked
                fetchTracks({source: 'local'}).then(tracks => {
                    currentTracks = tracks.filter(t => t.liked);
                    renderTracklist(currentTracks);
                });
            }
        });
    });

    // Player Controls
    btnPlay.addEventListener('click', togglePlay);
    btnNext.addEventListener('click', nextTrack);
    btnPrev.addEventListener('click', prevTrack);

    // Like Toggle (Delegation)
    tracklist.addEventListener('click', async (e) => {
        const likeBtn = e.target.closest('.like-btn');
        if (!likeBtn) return;

        const trackId = parseInt(likeBtn.dataset.id);
        const isLiked = likeBtn.dataset.liked === 'true';
        
        await toggleLike(trackId, isLiked);
        
        // Update UI locally
        likeBtn.dataset.liked = !isLiked;
        likeBtn.querySelector('i').className = !isLiked ? 'fas fa-heart' : 'far fa-heart';
        
        // Update player button if it's the current track
        if (currentIndex !== -1 && currentTracks[currentIndex].id === trackId) {
            btnLikePlayer.querySelector('i').className = !isLiked ? 'fas fa-heart' : 'far fa-heart';
        }
    });

    btnLikePlayer.addEventListener('click', async () => {
        if (currentIndex === -1) return;
        const track = currentTracks[currentIndex];
        await toggleLike(track.id, track.liked);
        // Refresh current track state in memory and UI
        track.liked = !track.liked;
        btnLikePlayer.querySelector('i').className = track.liked ? 'fas fa-heart' : 'far fa-heart';
    });

    // Audio Progress
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

    // Volume
    volumeSlider.addEventListener('click', (e) => {
        const rect = volumeSlider.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        audioPlayer.volume = pos;
        volumeFill.style.width = `${pos * 100}%`;
    });

    // --- Initialization ---
    async function init() {
        try {
            const tracks = await fetchTracks();
            currentTracks = tracks;
            renderTracklist(tracks);
        } catch (err) {
            console.error(err);
            tracklist.innerHTML = '<div class="error">Failed to load library.</div>';
        }
    }

    init();
});


// playing-state-sync: синхронизация класса .playing и иконок
function setupPlayingSync() {
    const audioEl = document.querySelector('audio');
    if (!audioEl) return;

    audioEl.addEventListener('play', () => {
        document.querySelectorAll('.track-item').forEach(el => el.classList.remove('playing'));
        const active = document.querySelector('.track-item.active');
        if (active) active.classList.add('playing');

        const playBtnIcon = document.querySelector('#play-btn i, .play-btn i');
        if (playBtnIcon) {
            playBtnIcon.classList.remove('fa-play');
            playBtnIcon.classList.add('fa-pause');
        }
    });

    audioEl.addEventListener('pause', () => {
        document.querySelectorAll('.track-item.playing').forEach(el => el.classList.remove('playing'));
        const playBtnIcon = document.querySelector('#play-btn i, .play-btn i');
        if (playBtnIcon) {
            playBtnIcon.classList.remove('fa-pause');
            playBtnIcon.classList.add('fa-play');
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPlayingSync);
} else {
    setupPlayingSync();
}

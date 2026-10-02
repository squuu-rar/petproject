from typing import List, Dict, Any
from ytmusicapi import YTMusic
from db import get_db

class WaveEngine:
    """Engine for generating candidate tracks using YouTube Music radio functionality."""

    def __init__(self):
        self.ytmusic = YTMusic()

    async def generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Generates a deduped list of tracks from YouTube Music radio based on seed tracks.
        Each track is enriched with detailed metadata.
        """
        if not seed_track_ids:
            return []

        conn = get_db()
        try:
            # Get external_ids for the provided seed tracks
            placeholders = ','.join(['?'] * len(seed_track_ids))
            query = f"SELECT external_id FROM tracks WHERE id IN ({placeholders})"
            seed_rows = conn.execute(query, seed_track_ids).fetchall()
        finally:
            conn.close()

        external_ids = [row["external_id"] for row in seed_rows if row["external_id"]]
        if not external_ids:
            return []

        unique_candidates = {}

        for ext_id in external_ids:
            try:
                # Fetch the radio playlist starting from this video
                playlist = self.ytmusic.get_watch_playlist(ext_id)
                contents = playlist.get('contents', [])

                for item in contents:
                    # Extract videoId/browseId to identify the song
                    v_id = item.get('videoId') or item.get('browseId')
                    if not v_id or v_id in unique_candidates:
                        continue

                    # Extract preliminary metadata from playlist item
                    title = item.get('title')
                    artists_info = item.get('artists', [])
                    artist_name = artists_info[0].get('name') if artists_info else None
                    album_info = item.get('album', {})
                    album_name = album_info.get('name') if album_info else None
                    
                    thumbnails = item.get('thumbnails', [])
                    cover_url = thumbnails[-1]['url'] if thumbnails else None

                    unique_candidates[v_id] = {
                        "external_id": v_id,
                        "title": title,
                        "artist": artist_name,
                        "album": album_name,
                        "source": "remote",
                        "cover_path": cover_url,
                        "duration": None
                    }
            except Exception as e:
                # If radio is unavailable for a specific seed, move to the next
                continue

        final_results = []
        # Enrich candidates with full metadata via get_song
        for v_id, info in unique_candidates.items():
            try:
                song_meta = self.ytmusic.get_song(v_id)
                info.update({
                    "title": song_meta.get("title"),
                    "artist": song_meta.get("artists", [{}])[0].get("name") if song_meta.get("artists") else None,
                    "album": song_meta.get("album", {}).get("name") if song_meta.get("album") else None,
                    "duration": song_meta.get("duration"),
                })
                final_results.append(info)
            except Exception:
                # Fallback to playlist metadata if deep fetch fails
                if info["title"]:
                    final_results.append(info)

        return final_results

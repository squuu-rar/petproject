import asyncio
import random
import re
from typing import List, Dict, Any, Optional

from ytmusicapi import YTMusic
from db import (
    get_db,
    get_known_collection_keys,
    get_recently_skipped_artists,
    get_taste_profile_artists,
    get_wave_exclusions,
    sample_taste_tracks,
)
from lastfm_service import LastFMService

# Кэш соответствия (artist_lower, title_lower) -> YouTube videoId для локальных сидов из Яндекс Музыки,
# чтобы YTMusic Radio мгновенно строил волну по локальным MP3 без external_id
_LOCAL_SEED_VIDEO_CACHE: Dict[tuple[str, str], str] = {}

SPAM_TITLE_PATTERNS = re.compile(
    r"\b(?:karaoke|караоке|минус|минусовка|backing\s*track|ringtone|рингтон|8d\s*audio)\b",
    flags=re.IGNORECASE,
)


class WaveEngine:
    MAX_PARALLEL_SEARCHES = 5
    # 85% совершенно новых треков (открытия и рекомендации), максимум 15% знакомых треков из Любимого (2–3 на 20 песен)
    REMOTE_RATIO: float = 0.85

    def __init__(self):
        self.ytmusic = YTMusic()
        self.lastfm = LastFMService()

    @staticmethod
    def _parse_duration(duration_str: Optional[str]) -> Optional[float]:
        if duration_str is None:
            return None
        if isinstance(duration_str, (int, float)):
            return float(duration_str)
        if not isinstance(duration_str, str):
            return None
        try:
            parts = duration_str.strip().split(":")
            sec = 0.0
            for p in parts:
                sec = sec * 60 + float(p)
            return sec
        except Exception:
            return None

    @staticmethod
    def _upscale_cover(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        u = str(url).strip()
        u = re.sub(r"=w\d+-h\d+[^&\s]*", "=w540-h540-p-l90-rj", u)
        u = re.sub(r"=s\d+[^&\s]*", "=s540", u)
        return u

    def _resolve_seed_tracks(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        if not seed_track_ids:
            return []
        conn = get_db()
        try:
            placeholders = ",".join(["?"] * len(seed_track_ids))
            query = f"SELECT id, artist, title, genre, external_id FROM tracks WHERE id IN ({placeholders})"
            seed_rows = conn.execute(query, seed_track_ids).fetchall()
            return [dict(row) for row in seed_rows]
        finally:
            conn.close()

    def _search_youtube_stream(self, target_artist: str, target_title: str) -> Optional[Dict[str, Any]]:
        """Ищет трек строго по связке артист + песня с обязательной валидацией исполнителя."""
        query = f'"{target_artist}" {target_title}'
        try:
            res = self.ytmusic.search(query=query, filter="songs", limit=3)
            if not res:
                res = self.ytmusic.search(query=f"{target_artist} {target_title}", filter="songs", limit=3)
            if not res:
                return None

            target_artist_lower = target_artist.lower().strip()
            best_match = None

            for item in res:
                v_id = item.get("videoId")
                if not v_id:
                    continue

                artists = item.get("artists", [])
                item_artist = (artists[0].get("name") if artists else "").lower().strip()

                if target_artist_lower in item_artist or item_artist in target_artist_lower:
                    best_match = item
                    break

            if not best_match:
                return None

            v_id = best_match.get("videoId")
            if not v_id:
                return None

            artists = best_match.get("artists", [])
            artist_name = artists[0].get("name") if artists else target_artist
            album_info = best_match.get("album") or {}
            album_name = album_info.get("name") if isinstance(album_info, dict) else "-"
            thumbs = best_match.get("thumbnails", [])
            cover_url = self._upscale_cover(thumbs[-1]["url"]) if thumbs else None
            dur = item.get("duration_seconds") or self._parse_duration(best_match.get("duration"))

            return {
                "id": -1,
                "external_id": v_id,
                "path": f"https://www.youtube.com/watch?v={v_id}",
                "title": best_match.get("title") or target_title,
                "artist": artist_name,
                "album": album_name,
                "genre": "Wave (Last.fm)",
                "year": None,
                "track_number": None,
                "cover_path": cover_url,
                "duration": float(dur) if dur else None,
                "source": "remote",
                "cache_path": None,
                "liked": False,
                "last_accessed": None,
            }
        except Exception:
            return None

    def _resolve_video_id_for_seed(self, seed: Dict[str, Any]) -> Optional[str]:
        """Для локального трека из Яндекс Музыки (у которого external_id=None) находит его videoId в YTMusic,
        чтобы запустить радио-рекомендации по этому треку.
        """
        ext = seed.get("external_id")
        if ext and not str(ext).startswith("http"):
            return str(ext)

        artist = (seed.get("artist") or "").strip()
        title = (seed.get("title") or "").strip()
        if not artist or not title:
            return None

        cache_key = (artist.lower(), title.lower())
        if cache_key in _LOCAL_SEED_VIDEO_CACHE:
            return _LOCAL_SEED_VIDEO_CACHE[cache_key]

        matched = self._search_youtube_stream(artist, title)
        if matched and matched.get("external_id"):
            vid = str(matched["external_id"])
            _LOCAL_SEED_VIDEO_CACHE[cache_key] = vid
            return vid
        return None

    async def _get_similar_by_artist(self, artist: str, forbidden_titles: set) -> List[Dict[str, str]]:
        if not artist or not self.lastfm.api_key:
            return []

        def _fetch():
            data = self.lastfm._sync_request({
                "method": "artist.getsimilar",
                "artist": artist,
                "limit": 6,
                "autocorrect": 1,
            })
            similar_artists = data.get("similarartists", {}).get("artist", [])
            if isinstance(similar_artists, dict):
                similar_artists = [similar_artists]

            recommendations = []
            for sim in similar_artists:
                sim_name = sim.get("name")
                if not sim_name or sim_name.lower() == artist.lower():
                    continue

                top_data = self.lastfm._sync_request({
                    "method": "artist.gettoptracks",
                    "artist": sim_name,
                    "limit": 2,
                    "autocorrect": 1,
                })
                tracks = top_data.get("toptracks", {}).get("track", [])
                if isinstance(tracks, dict):
                    tracks = [tracks]

                for t in tracks:
                    t_title = t.get("name")
                    if t_title and t_title.lower() not in forbidden_titles:
                        recommendations.append({"artist": sim_name, "title": t_title})
            return recommendations

        return await asyncio.to_thread(_fetch)

    def _discover_from_ytmusic_taste(
        self,
        taste_artists: List[str],
        forbidden_titles: set,
        known_collection: set[tuple[str, str]],
        skipped_artists: set[str],
    ) -> List[Dict[str, Any]]:
        """Находит новые треки через YTMusic (похожие исполнители + незнакомые песни любимых артистов),
        работает без API-ключа Last.fm и идеально подходит для русского рока/панка/инди/альтернативы.
        """
        if not taste_artists:
            return []

        valid_taste = [a for a in taste_artists if a.lower().strip() not in skipped_artists]
        if not valid_taste:
            valid_taste = taste_artists

        # Берём 2 артиста из ядра вкуса (топ-15) и 2 из более широкого круга (15..60) для свежести
        top_slice = valid_taste[:15]
        mid_slice = valid_taste[15:60]
        chosen_artists: List[str] = []
        if top_slice:
            chosen_artists.extend(random.sample(top_slice, min(2, len(top_slice))))
        if mid_slice:
            chosen_artists.extend(random.sample(mid_slice, min(2, len(mid_slice))))

        discovered: List[Dict[str, Any]] = []

        for art_name in chosen_artists[:3]:
            try:
                # 1. Ищем профиль артиста в YTMusic, чтобы достать похожих исполнителей (related)
                art_search = self.ytmusic.search(art_name, filter="artists", limit=1) or []
                related_names: List[str] = []
                if art_search and art_search[0].get("browseId"):
                    try:
                        info = self.ytmusic.get_artist(art_search[0]["browseId"]) or {}
                        rel_results = (info.get("related") or {}).get("results") or []
                        for rel in rel_results[:5]:
                            r_title = (rel.get("title") or "").strip()
                            if r_title and r_title.lower() not in skipped_artists:
                                related_names.append(r_title)
                    except Exception:
                        pass

                # Выбираем 2 похожих артиста + самого артиста (для поиска его песен, которых ещё нет в Любимом)
                query_targets = random.sample(related_names, min(2, len(related_names))) + [art_name]
                for target_art in query_targets:
                    songs = self.ytmusic.search(target_art, filter="songs", limit=6) or []
                    added_for_target = 0
                    for item in songs:
                        v_id = item.get("videoId")
                        title = (item.get("title") or "").strip()
                        if not v_id or not title:
                            continue
                        if SPAM_TITLE_PATTERNS.search(title):
                            continue
                        t_low = title.lower()
                        if t_low in forbidden_titles:
                            continue

                        artists = item.get("artists") or []
                        item_artist = (artists[0].get("name") if artists else target_art).strip()
                        a_low = item_artist.lower()
                        if a_low in skipped_artists:
                            continue

                        # Главное правило: не предлагаем песню, которая УЖЕ лежит в Любимом!
                        if (a_low, t_low) in known_collection:
                            continue

                        dur = item.get("duration_seconds") or self._parse_duration(item.get("duration"))
                        if dur and (dur < 45 or dur > 720):
                            continue

                        thumbs = item.get("thumbnails") or []
                        cover_url = self._upscale_cover(thumbs[-1]["url"]) if thumbs else None
                        album_info = item.get("album") or {}
                        album_name = album_info.get("name") if isinstance(album_info, dict) else "-"

                        discovered.append({
                            "id": -1,
                            "external_id": v_id,
                            "path": f"https://www.youtube.com/watch?v={v_id}",
                            "title": title,
                            "artist": item_artist,
                            "album": album_name or "-",
                            "genre": "Wave (Discovery)",
                            "year": None,
                            "track_number": None,
                            "cover_path": cover_url,
                            "duration": float(dur) if dur else None,
                            "source": "remote",
                            "cache_path": None,
                            "liked": False,
                            "last_accessed": None,
                        })
                        added_for_target += 1
                        if added_for_target >= 2:
                            break
            except Exception:
                continue

        return discovered

    async def generate_candidates(self, seed_track_ids: List[int]) -> List[Dict[str, Any]]:
        if not seed_track_ids:
            return []

        seeds = await asyncio.to_thread(self._resolve_seed_tracks, seed_track_ids)
        if not seeds:
            return []

        known_collection, blocked_ids, skipped_artists, taste_artists = await asyncio.gather(
            asyncio.to_thread(get_known_collection_keys),
            asyncio.to_thread(get_wave_exclusions),
            asyncio.to_thread(get_recently_skipped_artists),
            asyncio.to_thread(get_taste_profile_artists, 60),
        )

        seed_ext_ids = {s["external_id"] for s in seeds if s.get("external_id")}
        seed_titles = {s["title"].lower().strip() for s in seeds if s.get("title")}
        blocked_ids = set(blocked_ids) | seed_ext_ids

        sem = asyncio.Semaphore(self.MAX_PARALLEL_SEARCHES)

        async def resolve(item: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                return await asyncio.to_thread(self._search_youtube_stream, item["artist"], item["title"])

        # 1. Last.fm (если настроен API-ключ)
        candidate_tasks = []
        for s in seeds[:3]:
            if s.get("artist"):
                candidate_tasks.append(self._get_similar_by_artist(s["artist"], seed_titles))

        gathered_groups = await asyncio.gather(*candidate_tasks, return_exceptions=True)
        raw_candidates = [t for group in gathered_groups if isinstance(group, list) for t in group]

        resolved = await asyncio.gather(*(resolve(c) for c in raw_candidates), return_exceptions=True)
        lastfm_tracks = [r for r in resolved if isinstance(r, dict)]

        # 2. YTMusic Watch Radio (автоматически находит videoId даже для локальных MP3 из Яндекс Музыки!)
        def get_yt_radio() -> List[Dict[str, Any]]:
            found: List[Dict[str, Any]] = []
            for s in seeds[:3]:
                ext_id = self._resolve_video_id_for_seed(s)
                if not ext_id:
                    continue
                try:
                    playlist = self.ytmusic.get_watch_playlist(videoId=ext_id, radio=True, limit=12)
                except Exception:
                    continue
                items = playlist.get("tracks") or playlist.get("contents") or []
                for item in items:
                    v_id = item.get("videoId")
                    title = (item.get("title") or "").strip()
                    if not v_id or not title:
                        continue
                    if SPAM_TITLE_PATTERNS.search(title):
                        continue
                    t_low = title.lower()
                    if t_low in seed_titles:
                        continue
                    artists = item.get("artists", [])
                    artist_name = (artists[0].get("name") if artists else "Unknown Artist").strip()
                    a_low = artist_name.lower()
                    if a_low in skipped_artists:
                        continue
                    # Исключаем треки, которые уже есть в Любимом / локальной коллекции
                    if (a_low, t_low) in known_collection:
                        continue
                    dur = item.get("duration_seconds") or self._parse_duration(item.get("length") or item.get("duration"))
                    if dur and (dur < 45 or dur > 720):
                        continue
                    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                    cover_url = self._upscale_cover(thumbs[-1]["url"]) if thumbs else None
                    found.append({
                        "id": -1,
                        "external_id": v_id,
                        "path": f"https://www.youtube.com/watch?v={v_id}",
                        "title": title,
                        "artist": artist_name,
                        "album": item.get("album", {}).get("name") if isinstance(item.get("album"), dict) else "-",
                        "genre": "Wave (Radio)",
                        "year": None,
                        "track_number": None,
                        "cover_path": cover_url,
                        "duration": float(dur) if dur else None,
                        "source": "remote",
                        "cache_path": None,
                        "liked": False,
                        "last_accessed": None,
                    })
            return found

        # 3. Запускаем YTMusic Radio и YTMusic Taste Discovery параллельно
        radio_future = asyncio.to_thread(get_yt_radio)
        taste_future = asyncio.to_thread(
            self._discover_from_ytmusic_taste,
            taste_artists,
            seed_titles,
            known_collection,
            skipped_artists,
        )
        radio_res, taste_res = await asyncio.gather(radio_future, taste_future, return_exceptions=True)
        radio_tracks = radio_res if isinstance(radio_res, list) else []
        taste_tracks = taste_res if isinstance(taste_res, list) else []

        # Перемешиваем и чередуем каналы рекомендаций
        random.shuffle(radio_tracks)
        random.shuffle(taste_tracks)
        random.shuffle(lastfm_tracks)

        merged: List[Dict[str, Any]] = []
        max_len = max(len(radio_tracks), len(taste_tracks), len(lastfm_tracks), 0)
        for i in range(max_len):
            if i < len(radio_tracks):
                merged.append(radio_tracks[i])
            if i < len(taste_tracks):
                merged.append(taste_tracks[i])
            if i < len(lastfm_tracks):
                merged.append(lastfm_tracks[i])

        candidates: Dict[str, Dict[str, Any]] = {}
        seen_artist_title: set[tuple[str, str]] = set()
        artist_counts: Dict[str, int] = {}

        for t in merged:
            ext_id = t.get("external_id")
            if not ext_id or ext_id in blocked_ids or ext_id in candidates:
                continue
            a_low = (t.get("artist") or "").strip().lower()
            t_low = (t.get("title") or "").strip().lower()
            if (a_low, t_low) in known_collection or (a_low, t_low) in seen_artist_title:
                continue
            # Не более 2 треков одного исполнителя на один батч волны
            if a_low and artist_counts.get(a_low, 0) >= 2:
                continue
            artist_counts[a_low] = artist_counts.get(a_low, 0) + 1
            seen_artist_title.add((a_low, t_low))
            candidates[ext_id] = t

        return list(candidates.values())

    @staticmethod
    def _interleave_wave(
        remote_tracks: List[Dict[str, Any]],
        familiar_tracks: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Формирует очередь так, чтобы:
        - Первые 3 трека всегда были свежими открытиями (remote_tracks)
        - Знакомые любимые треки (не более 15%, т.е. 2–3 штуки на 20 песен) вставлялись равномерно через каждые 5–6 новых треков
        - Один и тот же исполнитель не шёл дважды подряд
        """
        if not remote_tracks:
            return familiar_tracks[:limit]
        if not familiar_tracks:
            return remote_tracks[:limit]

        result: List[Dict[str, Any]] = []
        rem_idx = 0
        fam_idx = 0
        # Вставляем 1 знакомый трек после каждых 5 новых открытий
        step_new = max(4, round(len(remote_tracks) / max(1, len(familiar_tracks))))

        while len(result) < limit and (rem_idx < len(remote_tracks) or fam_idx < len(familiar_tracks)):
            for _ in range(step_new):
                if rem_idx < len(remote_tracks) and len(result) < limit:
                    result.append(remote_tracks[rem_idx])
                    rem_idx += 1
            if fam_idx < len(familiar_tracks) and len(result) < limit:
                result.append(familiar_tracks[fam_idx])
                fam_idx += 1

        # Лёгкая защита от двух треков одного артиста подряд
        for i in range(1, len(result) - 1):
            prev_art = (result[i - 1].get("artist") or "").strip().lower()
            cur_art = (result[i].get("artist") or "").strip().lower()
            if prev_art and prev_art == cur_art:
                for j in range(i + 1, len(result)):
                    swap_art = (result[j].get("artist") or "").strip().lower()
                    if swap_art != prev_art:
                        result[i], result[j] = result[j], result[i]
                        break

        return result[:limit]

    async def generate_wave(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = get_db()
        try:
            # Берём последний дослушанный или лайкнутый трек для мгновенной реакции волны
            recent = conn.execute("""
                SELECT track_id FROM history h
                JOIN tracks t ON t.id = h.track_id
                WHERE h.event IN ('finish', 'like', 'play')
                AND t.artist IS NOT NULL
                AND LENGTH(t.title) > 2
                ORDER BY
                    CASE WHEN h.event IN ('finish', 'like') THEN 0 ELSE 1 END ASC,
                    h.timestamp DESC
                LIMIT 2
            """).fetchall()
            recent_ids = [int(r["track_id"]) for r in recent]
        finally:
            conn.close()

        # Выбираем сиды из коллекции (разные артисты)
        sampled_taste = await asyncio.to_thread(
            sample_taste_tracks,
            max(limit, 10),
            1,
            set(recent_ids),
        )
        taste_seed_ids = [int(t["id"]) for t in sampled_taste[:3]]

        raw_seeds = recent_ids + taste_seed_ids
        if not raw_seeds:
            conn = get_db()
            try:
                fallback = conn.execute("""
                    SELECT id as track_id FROM tracks
                    WHERE artist IS NOT NULL AND LENGTH(title) > 2
                    ORDER BY id DESC LIMIT 3
                """).fetchall()
                raw_seeds = [int(r["track_id"]) for r in fallback]
            finally:
                conn.close()

        seeds = list(dict.fromkeys(raw_seeds))[:3]

        # 1. Генерируем пул НОВЫХ рекомендаций (85% волны)
        try:
            remote_pool = await self.generate_candidates(seeds)
        except Exception:
            remote_pool = []

        # 2. Строго ограничиваем количество треков из «Любимого» (максимум 15% = 2–3 трека на 20 песен)
        max_local = max(1, round(limit * (1.0 - self.REMOTE_RATIO)))  # Для limit=20 -> 3 трека
        num_remote = max(1, limit - max_local)                        # Для limit=20 -> 17 новых треков

        selected_remote = remote_pool[:num_remote]

        # Если внешние источники работали штатно (нашли хотя бы 6 новых треков),
        # НЕ забиваем остаток очереди старыми треками из Любимого сверх лимита max_local!
        if len(selected_remote) >= 5:
            allowed_local_count = max_local
        else:
            # Только если интернета совсем нет (офлайн-режим), играем из локальной медиатеки
            allowed_local_count = limit - len(selected_remote)

        seen_keys = {
            (str(t.get("artist") or "").strip().lower(), str(t.get("title") or "").strip().lower())
            for t in selected_remote
        }
        selected_local: List[Dict[str, Any]] = []
        for loc in sampled_taste:
            key = (str(loc.get("artist") or "").strip().lower(), str(loc.get("title") or "").strip().lower())
            if key not in seen_keys:
                selected_local.append(loc)
                seen_keys.add(key)
                if len(selected_local) >= allowed_local_count:
                    break

        return self._interleave_wave(selected_remote, selected_local, limit=limit)

import asyncio
import datetime
import random
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Any, Optional

from ytmusicapi import YTMusic
from db import (
    CYRILLIC_RE,
    _matches_language,
    get_db,
    get_known_collection_keys,
    get_realtime_session_signals,
    get_recently_skipped_artists,
    get_taste_profile_artists,
    get_wave_exclusions,
    sample_taste_tracks,
)
from lastfm_service import LastFMService
from music_metadata import Exclusions, enrich_tracks, track_key
from wave_feedback import VERSION

_LOCAL_SEED_VIDEO_CACHE: Dict[tuple[str, str], str] = {}

SPAM_TITLE_PATTERNS = re.compile(
    r"\b(?:karaoke|караоке|минус|минусовка|backing\s*track|ringtone|рингтон|8d\s*audio)\b",
    flags=re.IGNORECASE,
)

# Ключевые слова для классификации настроения/энергетики трека и выбора цвета волны
ENERGETIC_KEYWORDS = ("рок", "rock", "панк", "punk", "metal", "метал", "alternative", "альтернатив", "драйв", "phonk", "drum", "hard", "rave", "workout")
CALM_KEYWORDS = ("calm", "спокой", "ambient", "acoustic", "акустик", "lo-fi", "lofi", "focus", "chill", "jazz", "джаз", "piano", "instrumental", "инди", "indie")
SAD_DARK_KEYWORDS = ("post-punk", "пост-панк", "груст", "sad", "doomer", "dark", "меланхол", "бард", "shoegaze", "coldwave", "ночь", "night")
JOY_KEYWORDS = ("поп", "pop", "dance", "disco", "funk", "фанк", "summer", "бодр", "happy", "хит")


def detect_wave_context(
    device: Optional[str] = None,
    hour: Optional[int] = None,
    weekday: Optional[int] = None,
) -> Dict[str, Any]:
    """Определяет контекст слушателя: время суток, день недели и устройство (ПК/ноутбук vs телефон)."""
    now = datetime.datetime.now()
    h = hour if hour is not None else now.hour
    wd = weekday if weekday is not None else now.weekday()  # 0=Пн .. 6=Вс
    dev = (device or "desktop").lower()
    is_weekend = wd >= 5 or (wd == 4 and h >= 18)

    if 6 <= h < 11:
        time_of_day = "morning"
        time_label = "Утро"
    elif 11 <= h < 18:
        time_of_day = "day"
        time_label = "День"
    elif 18 <= h < 23:
        time_of_day = "evening"
        time_label = "Вечер"
    else:
        time_of_day = "night"
        time_label = "Ночь"

    # Автоматический профиль по контексту (как в Яндекс Музыке):
    # - Будний день с ноутбука/ПК -> спокойный сфокусированный поток для работы
    # - С телефона в пути или пятница/выходные -> бодрый и энергичный поток
    # - Ночь -> спокойный и атмосферный ночной поток
    if time_of_day == "night":
        auto_mood = "dark"
        auto_activity = "relax"
        context_hint = f"{time_label} · Атмосферный ночной поток"
    elif not is_weekend and dev == "desktop" and time_of_day in ("morning", "day"):
        auto_mood = "calm"
        auto_activity = "work"
        context_hint = f"Будни · {time_label} · ПК (Спокойный поток для работы)"
    elif dev == "mobile" and time_of_day in ("morning", "day", "evening"):
        auto_mood = "energetic"
        auto_activity = "commute"
        context_hint = f"{'Выходные' if is_weekend else 'Будни'} · {time_label} · Смартфон (Бодрый поток)"
    elif is_weekend:
        auto_mood = "energetic"
        auto_activity = "relax"
        context_hint = f"Выходные · {time_label} · Энергичный микс"
    else:
        auto_mood = "calm"
        auto_activity = "relax"
        context_hint = f"Будни · {time_label} · Сбалансированный поток"

    return {
        "time_of_day": time_of_day,
        "time_label": time_label,
        "is_weekend": is_weekend,
        "device": dev,
        "auto_mood": auto_mood,
        "auto_activity": auto_activity,
        "context_hint": context_hint,
    }


def classify_track_vibe(
    track: Dict[str, Any],
    active_mood: Optional[str] = None,
    active_activity: Optional[str] = None,
) -> str:
    """Вычисляет цветовой характер трека для динамической анимации «Моей волны»:
    'energy' (кораллово-алый драйв), 'joy' (солнечно-золотой бодрый),
    'calm' (сапфирово-лазурный фокус), 'melancholy' (фиолетово-индиговый ночной/грустный).
    """
    if active_mood == "energetic" or active_activity == "workout":
        return "energy"
    if active_mood == "calm" or active_activity == "work":
        return "calm"
    if active_mood in ("sad", "dark"):
        return "melancholy"

    meta_text = f"{track.get('genre') or ''} {track.get('artist') or ''} {track.get('title') or ''} {track.get('album') or ''}".lower()
    if any(k in meta_text for k in SAD_DARK_KEYWORDS):
        return "melancholy"
    if any(k in meta_text for k in ENERGETIC_KEYWORDS):
        return "energy"
    if any(k in meta_text for k in JOY_KEYWORDS):
        return "joy"
    if any(k in meta_text for k in CALM_KEYWORDS):
        return "calm"

    dur = track.get("duration")
    if isinstance(dur, (int, float)):
        if dur < 175:
            return "energy"
        if dur > 270:
            return "melancholy"
    return "calm"


class WaveEngine:
    MAX_PARALLEL_SEARCHES = 5
    REMOTE_RATIO: float = 0.85  # По умолчанию 85% новых открытий, максимум 15% знакомых из Любимого

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
            target_title_key = self._title_key(target_title)
            best_match = None
            best_score = 0.0

            for item in res:
                v_id = item.get("videoId")
                if not v_id:
                    continue

                artists = item.get("artists", [])
                artist_names = {(a.get("name") or "").lower().strip() for a in artists}
                title_score = SequenceMatcher(None, target_title_key, self._title_key(item.get("title") or "")).ratio()
                if target_artist_lower and target_artist_lower in artist_names and title_score >= 0.85 and title_score > best_score:
                    best_match = item
                    best_score = title_score

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
            dur = best_match.get("duration_seconds") or self._parse_duration(best_match.get("duration"))

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

    @staticmethod
    def _title_key(title: str) -> str:
        title = re.sub(r"\b(?:official\s+(?:music\s+)?(?:video|audio)|lyrics?)\b", "", title.lower())
        return " ".join(re.findall(r"\w+", title))

    @staticmethod
    def _rank_tracks(tracks, taste_artists=(), positive_artists=(), mood=None, activity=None):
        """Soft metadata preference: missing genre data never excludes a track."""
        taste = {a.strip().lower(): 1.0 / (1.0 + i / 10.0) for i, a in enumerate(taste_artists)}
        positive = {a.strip().lower() for a in positive_artists}
        keywords = {
            "energetic": ENERGETIC_KEYWORDS, "calm": CALM_KEYWORDS,
            "sad": SAD_DARK_KEYWORDS, "dark": SAD_DARK_KEYWORDS,
        }.get(mood, ())
        activity_keywords = {
            "work": CALM_KEYWORDS, "relax": CALM_KEYWORDS,
            "workout": ENERGETIC_KEYWORDS, "commute": ENERGETIC_KEYWORDS,
        }.get(activity, ())

        def score(track):
            artist = (track.get("artist") or "").strip().lower()
            metadata = " ".join(str(track.get(k) or "") for k in ("title", "genre", "album")).lower()
            tags = " ".join(track.get("tags") or [])
            return (taste.get(artist, 0.0) + (1.5 if artist in positive else 0.0)
                    + (1.5 if any(k in metadata for k in keywords) else 0.0)
                    + (0.5 if any(k in metadata for k in activity_keywords) else 0.0)
                    + (2.0 if any(k in tags for k in keywords) else 0.0)
                    + (1.0 if any(k in tags for k in activity_keywords) else 0.0))

        return sorted(tracks, key=score, reverse=True)

    def _resolve_video_id_for_seed(self, seed: Dict[str, Any]) -> Optional[str]:
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

    @staticmethod
    def _build_context_search_modifier(
        activity: Optional[str],
        mood: Optional[str],
        language: Optional[str],
    ) -> Optional[str]:
        """Подбирает поисковые модификаторы для ручных настроек и контекста."""
        if language == "instrumental":
            return "instrumental post-rock ambient"
        mods = []
        if activity == "work":
            mods.append("спокойная для работы acoustic")
        elif activity == "workout":
            mods.append("энергичный драйв")
        elif activity == "commute":
            mods.append("в дорогу драйв")
        elif activity == "relax":
            mods.append("атмосферная чилл")

        if mood == "energetic":
            mods.append("бодрый рок драйв")
        elif mood == "calm":
            mods.append("спокойная акустика инди")
        elif mood == "sad":
            mods.append("грустная лирика меланхолия")
        elif mood == "dark":
            mods.append("пост-панк doomer dark")

        return " ".join(mods) if mods else None

    def _discover_from_ytmusic_taste(
        self,
        taste_artists: List[str],
        forbidden_titles: set,
        known_collection: set[tuple[str, str]],
        skipped_artists: set[str],
        activity: Optional[str] = None,
        mood: Optional[str] = None,
        character: Optional[str] = None,
        language: Optional[str] = None,
        shake: bool = False,
    ) -> List[Dict[str, Any]]:
        if not taste_artists:
            return []

        valid_taste = [a for a in taste_artists if a.lower().strip() not in skipped_artists]
        if not valid_taste:
            valid_taste = taste_artists

        if language in ("ru", "foreign"):
            lang_artists = [
                a for a in valid_taste
                if (bool(CYRILLIC_RE.search(a)) if language == "ru" else not bool(CYRILLIC_RE.search(a)))
            ]
            if lang_artists:
                valid_taste = lang_artists

        # В режиме «Встряхнуть волну» (shake=True) прыгаем в дальние кластеры вкуса (ранги 8..60)
        if shake and len(valid_taste) > 8:
            pool = valid_taste[6:]
            chosen_artists = random.sample(pool, min(4, len(pool)))
        else:
            top_slice = valid_taste[:15]
            mid_slice = valid_taste[15:60]
            chosen_artists = []
            if top_slice:
                chosen_artists.extend(random.sample(top_slice, min(2, len(top_slice))))
            if mid_slice:
                chosen_artists.extend(random.sample(mid_slice, min(2, len(mid_slice))))

        discovered: List[Dict[str, Any]] = []
        context_mod = self._build_context_search_modifier(activity, mood, language)

        for art_name in chosen_artists[:3]:
            try:
                art_search = self.ytmusic.search(art_name, filter="artists", limit=1) or []
                related_names: List[str] = []
                if art_search and art_search[0].get("browseId"):
                    try:
                        info = self.ytmusic.get_artist(art_search[0]["browseId"]) or {}
                        rel_results = (info.get("related") or {}).get("results") or []
                        for rel in rel_results[:6]:
                            r_title = (rel.get("title") or "").strip()
                            if r_title and r_title.lower() not in skipped_artists:
                                related_names.append(r_title)
                    except Exception:
                        pass

                if character == "discovery" and related_names:
                    query_targets = random.sample(related_names, min(3, len(related_names)))
                else:
                    query_targets = random.sample(related_names, min(2, len(related_names))) + [art_name]

                for target_art in query_targets:
                    q_str = f"{target_art} {context_mod}" if context_mod else target_art
                    songs = self.ytmusic.search(q_str, filter="songs", limit=6) or []
                    def matches_artist(item):
                        return target_art.strip().lower() in {
                            (artist.get("name") or "").strip().lower()
                            for artist in item.get("artists") or []
                        }

                    songs = [item for item in songs if matches_artist(item)]
                    if not songs and q_str != target_art:
                        songs = self.ytmusic.search(target_art, filter="songs", limit=6) or []
                        songs = [item for item in songs if matches_artist(item)]

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

                        if character != "favorite" and (a_low, t_low) in known_collection:
                            continue

                        cand_dict = {
                            "id": -1,
                            "external_id": v_id,
                            "path": f"https://www.youtube.com/watch?v={v_id}",
                            "title": title,
                            "artist": item_artist,
                            "album": (item.get("album") or {}).get("name") if isinstance(item.get("album"), dict) else "-",
                            "genre": "Wave (Discovery)",
                            "year": None,
                            "track_number": None,
                            "cover_path": self._upscale_cover((item.get("thumbnails") or [{}])[-1].get("url")),
                            "duration": float(item.get("duration_seconds") or self._parse_duration(item.get("duration")) or 0) or None,
                            "source": "remote",
                            "cache_path": None,
                            "liked": False,
                            "last_accessed": None,
                        }

                        if cand_dict["duration"] and (cand_dict["duration"] < 45 or cand_dict["duration"] > 720):
                            continue

                        discovered.append(cand_dict)
                        added_for_target += 1
                        if added_for_target >= 2:
                            break
            except Exception:
                continue

        return discovered

    async def generate_candidates(
        self,
        seed_track_ids: List[int],
        activity: Optional[str] = None,
        mood: Optional[str] = None,
        character: Optional[str] = None,
        language: Optional[str] = None,
        shake: bool = False,
        exclude_tracks: Optional[list] = None,
    ) -> List[Dict[str, Any]]:
        if not seed_track_ids:
            return []
        exclusions = Exclusions(exclude_tracks or [])

        seeds = await asyncio.to_thread(self._resolve_seed_tracks, seed_track_ids)
        if not seeds:
            return []

        known_collection, blocked_ids, skipped_artists, taste_artists, session_sig = await asyncio.gather(
            asyncio.to_thread(get_known_collection_keys),
            asyncio.to_thread(get_wave_exclusions),
            asyncio.to_thread(get_recently_skipped_artists),
            asyncio.to_thread(get_taste_profile_artists, 60),
            asyncio.to_thread(get_realtime_session_signals, 45.0),
        )

        # Объединяем долгосрочные и сиюминутные скипы, а при «Встряхнуть» исключаем и артистов текущей сессии!
        all_skipped_artists = set(skipped_artists) | set(session_sig.get("skipped_artists", set()))
        if shake:
            all_skipped_artists |= set(session_sig.get("recent_session_artists", set()))

        seed_ext_ids = {s["external_id"] for s in seeds if s.get("external_id")}
        seed_titles = {s["title"].lower().strip() for s in seeds if s.get("title")}
        blocked_ids = set(blocked_ids) | seed_ext_ids

        sem = asyncio.Semaphore(self.MAX_PARALLEL_SEARCHES)

        async def resolve(item: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                return await asyncio.to_thread(self._search_youtube_stream, item["artist"], item["title"])

        async def get_lastfm_tracks():
            async def similar(seed):
                tracks = await self.lastfm.get_similar_tracks(seed["artist"], seed.get("title") or "", limit=8)
                return tracks or await self._get_similar_by_artist(seed["artist"], seed_titles)

            groups = await asyncio.gather(*(similar(s) for s in seeds[:3] if s.get("artist")), return_exceptions=True)
            unique = {}
            for group in groups:
                if isinstance(group, list):
                    for track in group:
                        key = ((track.get("artist") or "").strip().lower(), (track.get("title") or "").strip().lower())
                        if all(key) and key[0] not in all_skipped_artists:
                            unique.setdefault(key, track)
            resolved = await asyncio.gather(*(resolve(c) for c in unique.values()), return_exceptions=True)
            return [r for r in resolved if isinstance(r, dict)]

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
                    if a_low in all_skipped_artists:
                        continue
                    if character != "favorite" and (a_low, t_low) in known_collection:
                        continue
                    dur = item.get("duration_seconds") or self._parse_duration(item.get("length") or item.get("duration"))
                    if dur and (dur < 45 or dur > 720):
                        continue
                    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
                    cover_url = self._upscale_cover(thumbs[-1]["url"]) if thumbs else None
                    cand = {
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
                    }
                    found.append(cand)
            return found

        radio_future = asyncio.to_thread(get_yt_radio)
        taste_future = asyncio.to_thread(
            self._discover_from_ytmusic_taste,
            taste_artists,
            seed_titles,
            known_collection,
            all_skipped_artists,
            activity,
            mood,
            character,
            language,
            shake,
        )
        radio_res, taste_res, lastfm_res = await asyncio.gather(
            radio_future, taste_future, get_lastfm_tracks(), return_exceptions=True,
        )
        radio_tracks = radio_res if isinstance(radio_res, list) else []
        taste_tracks = taste_res if isinstance(taste_res, list) else []
        lastfm_tracks = lastfm_res if isinstance(lastfm_res, list) else []

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

        merged = [t for t in merged if not exclusions.contains(t)]
        merged = await enrich_tracks(merged, getattr(self, "lastfm", None))
        ranked = self._rank_tracks(merged, taste_artists, session_sig.get("positive_artists") or [], mood, activity)
        for t in ranked:
            ext_id = t.get("external_id")
            if not ext_id or ext_id in blocked_ids or ext_id in candidates:
                continue
            a_low = (t.get("artist") or "").strip().lower()
            t_low = (t.get("title") or "").strip().lower()
            if not a_low or not t_low or a_low in all_skipped_artists:
                continue
            if SPAM_TITLE_PATTERNS.search(t_low) or not _matches_language(t, language):
                continue
            duration = self._parse_duration(t.get("duration"))
            if duration is not None and not 45 <= duration <= 720:
                continue
            if (a_low, t_low) in known_collection or (a_low, t_low) in seen_artist_title:
                continue
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
        # Spread both pools proportionally, including the favorite/offline modes.
        total = len(remote_tracks) + len(familiar_tracks)
        pending = []
        rem_idx = fam_idx = 0
        for i in range(total):
            if fam_idx < (i + 1) * len(familiar_tracks) // total:
                pending.append(familiar_tracks[fam_idx])
                fam_idx += 1
            else:
                pending.append(remote_tracks[rem_idx])
                rem_idx += 1

        def artist_key(track):
            return (track.get("artist") or "").strip().lower()

        result = []
        while pending and len(result) < limit:
            counts = Counter(artist_key(t) for t in pending)
            remaining = len(pending) - 1
            # Leave enough other artists to separate duplicates at the end as well.
            viable = [i for i, t in enumerate(pending) if all(
                count - (artist == artist_key(t)) <= (remaining + (artist != artist_key(t))) // 2
                for artist, count in counts.items()
            )]
            choices = viable or list(range(len(pending)))
            recent = {(t.get("artist") or "").strip().lower() for t in result[-2:]}
            index = next((i for i in choices if artist_key(pending[i]) not in recent), None)
            if index is None:
                previous = (result[-1].get("artist") or "").strip().lower() if result else ""
                index = next((i for i in choices if artist_key(pending[i]) != previous), choices[0])
            result.append(pending.pop(index))
        return result

    async def generate_wave(
        self,
        limit: int = 20,
        activity: Optional[str] = None,
        mood: Optional[str] = None,
        character: Optional[str] = None,
        language: Optional[str] = None,
        device: Optional[str] = None,
        shake: bool = False,
        offline: bool = False,
        exclude_tracks: Optional[list] = None,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        exclusions = Exclusions(exclude_tracks or [])

        def decorate(tracks):
            known = get_known_collection_keys()
            return [{**t, "is_discovery": track_key(t) not in known, "recommendation_version": VERSION}
                    for t in tracks if not exclusions.contains(t)]
        # 1. Определяем контекст (время суток, будни/выходные, устройство)
        ctx = detect_wave_context(device=device)
        eff_activity = activity if (activity and activity != "all") else ctx["auto_activity"]
        eff_mood = mood if (mood and mood != "all") else ctx["auto_mood"]
        eff_character = character if (character and character != "all") else "all"
        eff_language = language if (language and language != "all") else "all"

        # 2. Анализируем действия в текущей сессии (дослушивания, лайки, серия скипов)
        session_sig = await asyncio.to_thread(get_realtime_session_signals, 45.0)
        auto_shake = shake or (session_sig.get("skip_streak", 0) >= 2)

        # 3. Если включён Офлайн-режим (TinyML по локальной коллекции и кэшу), мгновенно строим умную очередь
        if offline:
            offline_tracks = await asyncio.to_thread(
                sample_taste_tracks,
                limit,
                1,
                None,
                eff_language,
                True,
                exclude_tracks,
            )
            offline_tracks = self._rank_tracks(
                offline_tracks, positive_artists=session_sig.get("positive_artists") or [],
                mood=eff_mood, activity=eff_activity,
            )
            return await asyncio.to_thread(decorate, self._interleave_wave([], offline_tracks, limit))

        # 4. Формируем сиды: приоритет у только что дослушанных/лайкнутых треков в текущей сессии
        session_pos_ids = session_sig.get("positive_track_ids") or []
        if auto_shake:
            # При встряхивании не берём последние треки сессии, а прыгаем в свежие сиды
            session_pos_ids = []

        sampled_taste = await asyncio.to_thread(
            sample_taste_tracks,
            max(limit * 3, 36),
            1,
            set(session_pos_ids),
            None,
            False,
            exclude_tracks,
        )
        sampled_taste = await enrich_tracks(sampled_taste, getattr(self, "lastfm", None))
        sampled_taste = [t for t in sampled_taste if _matches_language(t, eff_language) and not exclusions.contains(t)]
        taste_seed_ids = [int(t["id"]) for t in sampled_taste[:3]]
        raw_seeds = session_pos_ids[:2] + taste_seed_ids

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

        # 5. Определяем пропорцию новых треков в зависимости от настройки Характера («Незнакомое» / «Любимое» / «Баланс»)
        if eff_character == "favorite":
            remote_ratio = 0.25  # 75% любимых треков из коллекции
        elif eff_character == "discovery":
            remote_ratio = 0.95  # 95% совершенно новых треков
        else:
            remote_ratio = self.REMOTE_RATIO  # 85% новых открытий, максимум 15% знакомых (2–3 трека на 20)

        max_local = round(limit * (1.0 - remote_ratio))
        num_remote = limit - max_local

        # 6. Запрашиваем новые рекомендации
        remote_pool = []
        try:
            remote_pool = await self.generate_candidates(
                seeds,
                activity=eff_activity,
                mood=eff_mood,
                character=eff_character,
                language=eff_language,
                shake=auto_shake,
                exclude_tracks=exclude_tracks,
            )
            remote_pool = [t for t in remote_pool if not exclusions.contains(t)]
            # Retry once with different seeds when queue exclusions exhausted this batch.
            alternatives = [t["id"] for t in sampled_taste if t["id"] not in seeds][:3]
            if exclude_tracks and len(remote_pool) < num_remote and alternatives:
                more = await self.generate_candidates(
                    alternatives, activity=eff_activity, mood=eff_mood, character=eff_character,
                    language=eff_language, shake=auto_shake,
                    exclude_tracks=list(exclude_tracks) + remote_pool,
                )
                seen = Exclusions(list(exclude_tracks) + remote_pool)
                for track in more:
                    if not seen.contains(track):
                        remote_pool.append(track)
                        seen = Exclusions(list(exclude_tracks) + remote_pool)
        except Exception:
            pass  # Preserve a successful first batch if only the retry failed.

        sampled_taste = self._rank_tracks(
            sampled_taste, positive_artists=session_sig.get("positive_artists") or [],
            mood=eff_mood, activity=eff_activity,
        )
        selected_remote = remote_pool[:num_remote]

        # Если интернет есть и внешние рекомендации найдены, строго соблюдаем лимит max_local;
        # если интернета нет (офлайн-фоллбэк TinyML), бесшовно заполняем очередь из локальных и закэшированных треков!
        if len(selected_remote) >= 5:
            allowed_local_count = max_local
        else:
            allowed_local_count = limit - len(selected_remote)

        seen_keys = {
            (str(t.get("artist") or "").strip().lower(), str(t.get("title") or "").strip().lower())
            for t in selected_remote
        }
        selected_local: List[Dict[str, Any]] = []
        for loc in sampled_taste:
            if len(selected_local) >= allowed_local_count:
                break
            key = (str(loc.get("artist") or "").strip().lower(), str(loc.get("title") or "").strip().lower())
            if key not in seen_keys:
                selected_local.append(loc)
                seen_keys.add(key)
                if len(selected_local) >= allowed_local_count:
                    break

        # Use more discoveries when the library cannot satisfy the familiar quota.
        for track in remote_pool[num_remote:]:
            if len(selected_remote) + len(selected_local) >= limit:
                break
            key = ((track.get("artist") or "").strip().lower(), (track.get("title") or "").strip().lower())
            if key not in seen_keys:
                selected_remote.append(track)
                seen_keys.add(key)

        return await asyncio.to_thread(decorate, self._interleave_wave(selected_remote, selected_local, limit=limit))

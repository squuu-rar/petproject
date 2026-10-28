"""Единые настройки yt-dlp для поиска, определения ссылки и загрузки в кэш.

Переопределяется переменными окружения:
    YTDLP_YT_CLIENTS=android,web   клиенты YouTube (пусто = по умолчанию yt-dlp)
    YTDLP_NO_CHECK_CERT=0          включить проверку TLS-сертификатов
    YTDLP_PROXY=socks5://...       опциональный прокси для yt-dlp
"""
from __future__ import annotations

import os
from typing import Any, Dict

YT_PLAYER_CLIENTS = [
    c.strip() for c in os.environ.get("YTDLP_YT_CLIENTS", "android,ios,web").split(",") if c.strip()
]
NO_CHECK_CERT = os.environ.get("YTDLP_NO_CHECK_CERT", "1").strip().lower() not in ("0", "false", "no")
YTDLP_PROXY = os.environ.get("YTDLP_PROXY", "").strip()

AUDIO_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"


def _base() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": NO_CHECK_CERT,
        "source_address": "0.0.0.0",
        "geo_bypass": True,
    }
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    if YT_PLAYER_CLIENTS:
        opts["extractor_args"] = {"youtube": {"player_client": YT_PLAYER_CLIENTS}}
    return opts


def search_opts(timeout: int = 8) -> Dict[str, Any]:
    """Быстрый «плоский» поиск без разбора каждого трека."""
    opts = _base()
    opts.update({
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": timeout,
        "retries": 1,
    })
    return opts


def stream_opts() -> Dict[str, Any]:
    """Определение прямой ссылки на звук без скачивания."""
    opts = _base()
    opts.update({
        "format": AUDIO_FORMAT,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 10,
        "retries": 2,
    })
    return opts


def download_opts(outtmpl: str, fast_fail: bool = False) -> Dict[str, Any]:
    """Загрузка в файл кэша. Расширение подставляет yt-dlp (%(ext)s)."""
    opts = _base()
    opts.update({
        "format": AUDIO_FORMAT,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "socket_timeout": 8 if fast_fail else 12,
        "retries": 1 if fast_fail else 3,
        "fragment_retries": 1 if fast_fail else 3,
        "http_chunk_size": 5 * 1024 * 1024,
    })
    return opts

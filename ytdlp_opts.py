"""Единые настройки yt-dlp для поиска, определения ссылки и загрузки в кэш.

Раньше они были разбросаны по файлам и расходились: кэш качал с клиентами
android/web и повторами, а ссылка для воспроизведения определялась с настройками
по умолчанию. Из-за этого первый запуск трека падал, а повторный (уже из кэша)
проходил. Теперь все три места берут настройки отсюда.

Переопределяется переменными окружения:
    YTDLP_YT_CLIENTS=android,web   клиенты YouTube (пусто = по умолчанию yt-dlp)
    YTDLP_NO_CHECK_CERT=0          включить проверку TLS-сертификатов
"""
from __future__ import annotations

import os
from typing import Any, Dict

YT_PLAYER_CLIENTS = [
    c.strip() for c in os.environ.get("YTDLP_YT_CLIENTS", "android,web").split(",") if c.strip()
]
NO_CHECK_CERT = os.environ.get("YTDLP_NO_CHECK_CERT", "1").strip().lower() not in ("0", "false", "no")

# Сначала «прогрессивный» звук по обычному http(s): <audio> не умеет HLS (.m3u8),
# а SoundCloud по умолчанию часто отдаёт именно его. Если такого формата нет,
# берём что есть: последние два варианта подстрахуют, а не заменят первый.
AUDIO_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"


def _base() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": NO_CHECK_CERT,
    }
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
        "socket_timeout": 15,
        "retries": 3,
    })
    return opts


def download_opts(outtmpl: str) -> Dict[str, Any]:
    """Загрузка в файл кэша. Расширение подставляет yt-dlp (%(ext)s)."""
    opts = _base()
    opts.update({
        "format": AUDIO_FORMAT,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 10,
        "fragment_retries": 10,
        "http_chunk_size": 5 * 1024 * 1024,
    })
    return opts

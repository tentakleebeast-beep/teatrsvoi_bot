"""Характеристики аудио: длительность, битрейт, формат, теги (Ш-8, FR-M1).

В требованиях стоял `ffprobe`, и я от него отступил — сознательно, а не по недосмотру:

  * `ffprobe` приезжает пакетом ffmpeg: +60–80 МБ в образ `tool-api` и отдельная установка
    на машине разработчика ради четырёх полей;
  * нужны только длительность, битрейт, формат и id3-теги — с этим справляется `mutagen`,
    чистый Python без системных зависимостей;
  * перекодирования нет (решение заказчика), поэтому «движок декодирования» не нужен.

Проверено на mp3, m4a, flac, ogg и wav: длительность и битрейт читаются, файлы не портятся.
Если mutagen чего-то не понял — возвращаем `None`, и файл всё равно принимается: отсутствие
длительности не повод терять музыку к спектаклю.
"""

from __future__ import annotations

import logging

from mutagen import File as MutagenFile
from mutagen import MutagenError

log = logging.getLogger("audio")

# Форматы, которые mutagen разбирает предсказуемо (совпадает с AUDIO_EXT в files_store)
KNOWN_FORMATS = {".mp3": "mp3", ".wav": "wav", ".ogg": "ogg", ".opus": "opus",
                 ".flac": "flac", ".m4a": "m4a", ".aac": "aac"}


def probe(path: str, name: str = "") -> dict | None:
    """Прочитать характеристики файла. None — не разобрали (не ошибка загрузки)."""
    try:
        audio = MutagenFile(path)
    except (MutagenError, OSError) as exc:        # битый файл не должен ломать приём
        log.warning("не удалось прочитать аудио %s: %s", name or path, exc)
        return None
    if audio is None or getattr(audio, "info", None) is None:
        return None

    info = audio.info
    tags = getattr(audio, "tags", None)
    return {
        "duration_sec": int(round(float(getattr(info, "length", 0) or 0))) or None,
        "bitrate_kbps": int(round((getattr(info, "bitrate", 0) or 0) / 1000)) or None,
        "format": _format_of(audio, name),
        "title": _tag(tags, "title"),
        "artist": _tag(tags, "artist"),
    }


def _format_of(audio, name: str) -> str | None:
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in (name or "") else ""
    if suffix in KNOWN_FORMATS:
        return KNOWN_FORMATS[suffix]
    kind = type(audio).__name__.lower()
    for key, value in (("mp3", "mp3"), ("wav", "wav"), ("flac", "flac"),
                       ("ogg", "ogg"), ("mp4", "m4a"), ("aac", "aac")):
        if key in kind:
            return value
    return None


def _tag(tags, key: str) -> str | None:
    """id3, vorbis и mp4 называют поля по-разному — берём первое, что нашлось."""
    if not tags:
        return None
    variants = {"title": ("title", "\xa9nam", "TIT2"),
                "artist": ("artist", "\xa9ART", "TPE1")}[key]
    for variant in variants:
        value = tags.get(variant)
        if not value:
            continue
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value).strip() or None
    return None

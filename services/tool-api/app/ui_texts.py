"""
Тексты интерфейса из файла, а не из кода.

Заказчик правит формулировки сам (`config/ui-texts.yaml`), не дожидаясь релиза.
Поэтому файл читается при обращении, с кэшем по времени изменения: поправили
строку — следующая же страница отдаёт новый текст.

Ошибка в файле не должна ломать интерфейс: битый YAML, пропавшая строка или
пустое значение — берём текст по умолчанию из кода и живём дальше.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "ui-texts.yaml"
_CACHE: dict[str, Any] = {"stamp": None, "data": {}}


def path() -> Path:
    return Path(os.getenv("UI_TEXTS_PATH") or DEFAULT_PATH)


def _stamp(file: Path) -> tuple[float, int] | None:
    try:
        stat = file.stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def texts() -> dict:
    """Словарь текстов; при любой проблеме — пустой (значит, работают умолчания)."""
    file = path()
    stamp = _stamp(file)
    if stamp is None:
        return {}
    if _CACHE["stamp"] == stamp:
        return _CACHE["data"]
    try:
        import yaml
    except ImportError:                       # PyYAML нет — не повод падать
        return {}
    try:
        loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return _CACHE["data"] if _CACHE["data"] else {}
    data = loaded if isinstance(loaded, dict) else {}
    _CACHE["stamp"], _CACHE["data"] = stamp, data
    return data


def get(section: str, key: str, default: str) -> str:
    """Строка из секции или значение по умолчанию; пустое значение — тоже умолчание."""
    value = (texts().get(section) or {}).get(key)
    if isinstance(value, str) and value.strip():
        return value
    return default


def section(name: str) -> dict[str, str]:
    """Все строки секции строками (числа и None отбрасываем)."""
    raw = texts().get(name) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()
            if isinstance(value, (str, int, float)) and str(value).strip()}

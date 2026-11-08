"""Кэш повторяющихся ответов агента с малым TTL (A-4, docs/development/108 §10).

Зачем. Одни и те же вопросы приходят пачками: «какое сегодня число», «кто сегодня
занят». Ответ на них живёт секунды, а каждый вызов модели стоит токенов и времени.
Кэш на 30–60 секунд снимает повторный вызов и не превращается в источник старых данных.

Правила, без которых кэш вреден:
  * ключ — область (чат, иначе человек) + нормализованный текст: чужой ответ не
    пересекается со своим, а «Кто сегодня занят?» и «кто сегодня занят» — один вопрос;
  * кэшируются только ответы без предложения записи (`propose`): карточка
    подтверждения обязана появляться заново, иначе человек нажмёт кнопку по старому
    черновику;
  * TTL берётся из `config/agent-models.yaml` по группам: дата/время — 30 секунд,
    занятость — 60; вопросы вне групп не кэшируются вовсе (`default_ttl_seconds: 0`),
    потому что бюджет, взносы и расписание меняются;
  * кэш живёт в памяти процесса: это экономия на повторе, а не хранилище. Перезапуск
    его очищает, несколько воркеров кэшируют каждый сам — цена этого решения ниже,
    чем у общего хранилища с блокировками и инвалидацией.

Кэш не влияет на бюджет: попадание не тратит ни шага, ни токена (`step_count = 0`),
поэтому и в дневной счёт (`agently/budget.py`) не попадает.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from . import models

_SPACES = re.compile(r"\s+")
_TRAILING = " \t.,!?;:—-"
_LOCK = threading.Lock()
_ENTRIES: dict[str, dict] = {}
_HITS = 0
_MISSES = 0


def normalize(text: str) -> str:
    """Нормализованный текст вопроса: регистр, ё, пробелы, хвостовая пунктуация."""
    lowered = (text or "").lower().replace("ё", "е")
    return _SPACES.sub(" ", lowered).strip().strip(_TRAILING)


def key(scope: str, text: str) -> str:
    return f"{scope}|{normalize(text)}"


def scope_of(chat_id: int | None, participant_id: int | None) -> str:
    """Область кэша: чат, а если его нет — человек. Без области ответы смешались бы."""
    if chat_id:
        return f"chat:{int(chat_id)}"
    if participant_id:
        return f"person:{int(participant_id)}"
    return ""


def ttl_for(text: str) -> float:
    """TTL вопроса по группам конфига; 0 — не кэшировать."""
    policy = models.cache_policy()
    question = normalize(text)
    if not question:
        return 0.0
    for group in policy["groups"]:
        if any(normalize(pattern) in question for pattern in group["patterns"]):
            return float(group["ttl_seconds"])
    return float(policy["default_ttl_seconds"])


def get(scope: str, text: str) -> dict | None:
    """Ответ из кэша, если TTL не истёк. Истёкшую запись удаляем сразу."""
    global _HITS, _MISSES
    if not scope:
        return None
    entry_key = key(scope, text)
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(entry_key)
        if entry is None:
            _MISSES += 1
            return None
        if entry["expires_at"] <= now:
            _ENTRIES.pop(entry_key, None)
            _MISSES += 1
            return None
        _HITS += 1
        return dict(entry["value"]) | {"cached": True,
                                       "age_seconds": round(now - entry["created_at"], 1)}


def put(scope: str, text: str, value: dict) -> float:
    """Положить ответ в кэш, если вопрос из группы и ответ безопасно повторять.

    Возвращает TTL, с которым запись легла (0 — не легла). Ответ с предложением
    записи не кэшируется никогда: это не факт, а черновик действия.
    """
    if not scope or value.get("propose"):
        return 0.0
    if not str(value.get("answer") or "").strip():
        return 0.0
    ttl = ttl_for(text)
    if ttl <= 0:
        return 0.0
    now = time.monotonic()
    payload = {"answer": value.get("answer"), "scenario": value.get("scenario"),
               "model": value.get("model"), "engine": value.get("engine")}
    with _LOCK:
        _ENTRIES[key(scope, text)] = {"value": payload, "created_at": now,
                                      "expires_at": now + ttl}
        if len(_ENTRIES) > 1000:      # защита от роста: чистим истёкшее и старое
            for stale in [name for name, item in _ENTRIES.items()
                          if item["expires_at"] <= now]:
                _ENTRIES.pop(stale, None)
    return ttl


def clear() -> None:
    """Очистить кэш (тесты и ручная проверка после смены данных)."""
    global _HITS, _MISSES
    with _LOCK:
        _ENTRIES.clear()
        _HITS = 0
        _MISSES = 0


def stats() -> dict[str, Any]:
    """Счётчики процесса: попадания, промахи, живые записи (для отчёта и тестов)."""
    with _LOCK:
        now = time.monotonic()
        alive = sum(1 for item in _ENTRIES.values() if item["expires_at"] > now)
        return {"hits": _HITS, "misses": _MISSES, "entries": alive,
                "size": len(_ENTRIES)}

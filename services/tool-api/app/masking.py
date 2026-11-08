"""Обезличивание аргументов инструментов для журнала разбора (A-5, docs/development/108 §6).

Почему отдельный модуль, а не «спрятать в одном месте»: аргументы пишет не человек,
а модель, и в них попадает всё, что модель увидела в вопросе — номер телефона, адрес
почты, идентификатор человека. Журнал нужен целиком (без аргументов разбор превращается
в гадание), но токены, пароли и персональные данные в нём оставаться не должны.

Правила:
  * ключ аргумента похож на секрет (`token`, `password`, `api_key`, `authorization`,
    `cookie`, `secret`, `session`) — значение заменяется маской целиком, независимо
    от типа: даже короткий `1` не отличить от настоящего ключа;
  * ключ похож на персональные данные (`phone`, `email`, `participant_id`, `chat_id`,
    `person_id`, `contact`) — значение маскируется (номер — по правилу `privacy.mask_phone`);
  * свободный текст (цель операции, поисковый запрос) чистится от телефонов и почты
    по шаблону: человек пишет «оплатил за Сашу, 8917…» и не думает о журнале.

Тексты вопросов и ответов не маскируются: они и есть предмет разбора (решение владельца,
карта A-5). Маска — `···`, та же, что у `privacy.mask_phone`, чтобы на экране не было
двух разных обозначений скрытого.
"""

from __future__ import annotations

import re
from typing import Any

from .privacy import MASK, mask_phone

# Ключи, значение которых не попадает в журнал никогда.
SECRET_KEYS = ("token", "password", "passwd", "secret", "api_key", "apikey",
               "authorization", "auth", "cookie", "session", "dsn")

# Ключи персональных данных: их значения маскируются, а не выбрасываются —
# по ним видно, что инструмент звали с чужим идентификатором.
PERSONAL_KEYS = ("phone", "email", "mail", "participant_id", "person_id", "chat_id",
                 "contact", "tg_chat_id")

PHONE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

MAX_DEPTH = 6


def _lower(key: Any) -> str:
    return str(key).lower()


def is_secret(key: Any) -> bool:
    """Ключ аргумента — секрет (токен, пароль, ключ API)."""
    lowered = _lower(key)
    return any(marker in lowered for marker in SECRET_KEYS)


def is_personal(key: Any) -> bool:
    """Ключ аргумента — персональные данные."""
    lowered = _lower(key)
    return any(marker in lowered for marker in PERSONAL_KEYS)


def mask_text(value: str) -> str:
    """Телефоны и адреса почты в свободном тексте — под маску."""
    masked = EMAIL.sub(MASK + "@" + MASK, value)
    return PHONE.sub(lambda match: mask_phone(match.group(0)) or MASK, masked)


def _mask_personal(value: Any) -> Any:
    if isinstance(value, str):
        maybe_phone = mask_phone(value)
        if maybe_phone != value:
            return maybe_phone
        return MASK if value.strip() else value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return MASK
    return MASK


def mask_value(key: Any, value: Any, depth: int = 0) -> Any:
    """Значение одного аргумента по его ключу."""
    if is_secret(key):
        return MASK
    if is_personal(key):
        return _mask_personal(value)
    return mask_args(value, depth + 1) if isinstance(value, (dict, list)) else mask_leaf(value)


def mask_leaf(value: Any) -> Any:
    """Скаляр без персонального ключа: чистим телефоны и почту.

    Число из 10–11 цифр без ключа — почти всегда телефон в аргументе от модели
    («позвони 8917…»): такие значения маскируем, суммы и количества не трогаем.
    """
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, int) and not isinstance(value, bool) and len(str(abs(value))) in (10, 11):
        return MASK
    return value


def mask_args(value: Any, depth: int = 0) -> Any:
    """Рекурсивно обезличить аргументы инструмента, сохранив их форму."""
    if depth > MAX_DEPTH:
        # Глубже не разбираем: форма важнее содержимого, а бесконечная вложенность
        # в журнале всё равно бесполезна.
        return MASK
    if isinstance(value, dict):
        return {str(key): mask_value(key, item, depth) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_args(item, depth + 1) for item in value]
    return mask_leaf(value)


def mask_steps(steps: list[dict] | None) -> list[dict]:
    """Шаги агента в вид для журнала: имена инструментов остаются, аргументы — нет."""
    result: list[dict] = []
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        step = {key: value for key, value in raw.items() if key != "args"}
        if "args" in raw:
            step["args"] = mask_args(raw.get("args") or {})
        result.append(step)
    return result

"""Общие настройки двух сервисов: поля, которые совпадают, и разбор окружения.

Зачем один модуль (решение docs/reports/131, план docs/reports/133): гейтвей и tool-api
держали по своей копии `_env`, а `TZ_OFFSET_HOURS` читали по-разному — `int` в tool-api
и `float` в гейтвее. Из-за этого `TZ_OFFSET_HOURS=4.5` роняло tool-api на старте, а
пустое значение роняло гейтвей. Теперь тип и умолчание смещения объявлены здесь один раз,
и на одном окружении оба сервиса ведут себя одинаково.

Сервисные настройки (адреса, таблицы, календари, Google) остаются в
`services/<сервис>/app/settings.py`: здесь только то, что у сервисов общее.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TZ_OFFSET_HOURS: float = 4.0   # Самара, UTC+4


def env_str(env: Mapping[str, str], name: str, default: str = "") -> str:
    """Семантика `os.getenv(name, default)`: пустое значение остаётся пустым."""
    value = env.get(name)
    return default if value is None else value


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Целое из окружения; мусор — ошибка запуска, а не тихое умолчание."""
    value = env_str(env, name)
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name}={value!r}: ожидалось целое число") from None


def env_float(env: Mapping[str, str], name: str, default: float) -> float:
    """Дробное из окружения; мусор — ошибка запуска, а не тихое умолчание."""
    value = env_str(env, name)
    if value == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name}={value!r}: ожидалось число") from None


@dataclass(frozen=True, slots=True)
class BaseSettings:
    """Поля, которые у гейтвея и tool-api совпадают по имени и смыслу."""

    telegram_token: str = ""
    admin_chat_id: int = 0
    tool_api_token: str = ""
    tz_offset_hours: float = DEFAULT_TZ_OFFSET_HOURS

    @property
    def tz(self) -> datetime.timezone:
        return datetime.timezone(datetime.timedelta(hours=self.tz_offset_hours))

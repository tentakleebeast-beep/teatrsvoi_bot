"""
Конфигурация tg-gateway: единственное место чтения окружения (R-4, docs/reports/79 этап A).

Раньше значения читались через `os.getenv` прямо в `main.py` на уровне модуля: умолчание
незаметно уезжало в прод, а тесты подменяли `os.environ` до импорта. Теперь все переменные
и умолчания собраны здесь, а `main.py` получает один объект `Settings`.
Общие поля и парсеры окружения — в `services/shared/settings.py`.

Гейтвей — исполняемый сервис (`python services/tg-gateway/app/main.py`), поэтому настройки
читаются один раз при старте процесса. Правило проверяется автоматически:
`tests/unit/test_settings_single_source.py` запрещает чтение окружения на уровне модуля
вне этого файла.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Mapping
from dataclasses import dataclass

import _bootstrap  # noqa: F401  # корень репозитория в sys.path: services.shared

from services.shared.settings import (  # noqa: E402
    DEFAULT_TZ_OFFSET_HOURS,
    BaseSettings,
    env_float,
    env_int,
    env_str,
)

DEFAULT_TOOL_API_URL = "http://127.0.0.1:8000"
DEFAULT_TOOL_API_TOKEN = "dev-token"       # только локаль: в проде задаётся в .env


@dataclass(frozen=True, slots=True)
class Settings(BaseSettings):
    """Настройки гейтвея. Значения по умолчанию — только для локальной разработки."""

    tool_api_url: str = DEFAULT_TOOL_API_URL
    webapp_base_url: str = ""              # адрес вида https://teatr-svoi.online
    tool_api_token: str = DEFAULT_TOOL_API_TOKEN

    @property
    def local_tz(self) -> datetime.timezone:
        """Историческое имя часового пояса студии: то же, что унаследованный `tz`."""
        return self.tz

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        src = os.environ if env is None else env
        return cls(
            telegram_token=env_str(src, "TELEGRAM_TOKEN"),
            tool_api_url=env_str(src, "TOOL_API_URL", DEFAULT_TOOL_API_URL),
            webapp_base_url=env_str(src, "WEBAPP_BASE_URL"),
            tool_api_token=env_str(src, "TOOL_API_TOKEN", DEFAULT_TOOL_API_TOKEN),
            admin_chat_id=env_int(src, "ADMIN_CHAT_ID", 0),
            tz_offset_hours=env_float(src, "TZ_OFFSET_HOURS", DEFAULT_TZ_OFFSET_HOURS),
        )

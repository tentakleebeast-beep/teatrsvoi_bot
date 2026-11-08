"""
Конфигурация tool-api: единственное место чтения окружения (R-4, docs/reports/79 этап A).

Раньше значения читались через `os.getenv` прямо в `main.py` на уровне модуля: умолчание
незаметно уезжало в прод, а тесты подменяли `os.environ` до импорта. Теперь все переменные
и умолчания собраны здесь; приложение получает объект `Settings` явно — через
`Settings.from_env()` в проде или `main.configure(Settings(...))` в тестах.

Правило проверяется автоматически: `tests/unit/test_settings_single_source.py` запрещает
чтение окружения на уровне модуля вне этого файла.

Общие поля и парсеры окружения — в `services/shared/settings.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from services.shared.settings import (
    DEFAULT_TZ_OFFSET_HOURS,
    BaseSettings,
    env_float,
    env_int,
    env_str,
)

DEFAULT_DATABASE_URL = "postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi"
DEFAULT_GOOGLE_SA_JSON = "secrets/google-sa.json"
DEFAULT_INVITE_TTL_DAYS = 7
DEFAULT_ZIP_MAX_BYTES = 100 * 1024 * 1024   # потолок архива папки (FR-M6, аудит ponytail)


@dataclass(frozen=True, slots=True)
class Settings(BaseSettings):
    """Настройки сервиса. Значения по умолчанию — только для локальной разработки."""

    database_url: str = DEFAULT_DATABASE_URL
    env_name: str = "dev"
    allow_dev_sheet_write: bool = False
    invite_ttl_days: int = DEFAULT_INVITE_TTL_DAYS
    webapp_secret: str = ""
    telegram_token: str = ""
    google_sa_json: str = DEFAULT_GOOGLE_SA_JSON
    budget_spreadsheet_id: str = ""
    schedule_spreadsheet_id: str = ""
    schedule_sheet_title: str = ""
    calendar_id_read: str = ""
    calendar_id_venue: str = ""
    calendar_id_draft: str = ""
    zip_max_bytes: int = DEFAULT_ZIP_MAX_BYTES

    def __post_init__(self) -> None:
        # Прод без сервисного токена не запускается: раньше это же правило стояло
        # в main.py, теперь оно рядом со значением по умолчанию.
        if self.env_name == "prod" and not self.tool_api_token:
            raise RuntimeError("TOOL_API_TOKEN не задан: в проде сервис без токена не запускается")

    @property
    def is_prod(self) -> bool:
        return self.env_name == "prod"

    @property
    def sheet_write_enabled(self) -> bool:
        """Запись в лист из dev-контура выключена: иначе тестовые операции уезжают
        в боевую таблицу (docs/reports/84, карта t_a422101b). Включается явно
        ALLOW_DEV_SHEET_WRITE=1."""
        return self.env_name != "dev" or self.allow_dev_sheet_write

    @property
    def webapp_secret_effective(self) -> str:
        """Секрет подписи токена Mini App: WEBAPP_SECRET, иначе сервисный токен."""
        return self.webapp_secret or self.tool_api_token or "dev-webapp"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        src = os.environ if env is None else env
        env_name = env_str(src, "ENV", "dev")
        token = env_str(src, "TOOL_API_TOKEN")
        if not token and env_name != "prod":
            token = "dev-token"
            print("ВНИМАНИЕ: TOOL_API_TOKEN не задан, используется dev-token (только для локали)")
        return cls(
            tz_offset_hours=env_float(src, "TZ_OFFSET_HOURS", DEFAULT_TZ_OFFSET_HOURS),
            database_url=env_str(src, "DATABASE_URL", DEFAULT_DATABASE_URL),
            admin_chat_id=env_int(src, "ADMIN_CHAT_ID", 0),
            tool_api_token=token,
            env_name=env_name,
            allow_dev_sheet_write=env_str(src, "ALLOW_DEV_SHEET_WRITE") == "1",
            invite_ttl_days=env_int(src, "INVITE_TTL_DAYS", DEFAULT_INVITE_TTL_DAYS),
            webapp_secret=env_str(src, "WEBAPP_SECRET"),
            telegram_token=env_str(src, "TELEGRAM_TOKEN"),
            google_sa_json=env_str(src, "GOOGLE_SA_JSON", DEFAULT_GOOGLE_SA_JSON),
            budget_spreadsheet_id=env_str(src, "BUDGET_SPREADSHEET_ID"),
            schedule_spreadsheet_id=env_str(src, "SCHEDULE_SPREADSHEET_ID"),
            schedule_sheet_title=env_str(src, "SCHEDULE_SHEET_TITLE"),
            calendar_id_read=env_str(src, "CALENDAR_ID_READ"),
            calendar_id_venue=env_str(src, "CALENDAR_ID_VENUE"),
            calendar_id_draft=env_str(src, "CALENDAR_ID_DRAFT"),
            zip_max_bytes=env_int(src, "ZIP_MAX_BYTES", DEFAULT_ZIP_MAX_BYTES),
        )

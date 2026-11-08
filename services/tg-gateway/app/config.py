"""Настройки gateway: адреса сервисов, токен, часовой пояс."""

from __future__ import annotations

import logging
import sys

import _bootstrap  # noqa: F401  # корень репозитория в sys.path: services.shared
from settings import Settings

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")


# Все переменные окружения и умолчания — в settings.py (R-4); здесь только имена модуля.
settings = Settings.from_env()


TOKEN = settings.telegram_token


TOOL_API_URL = settings.tool_api_url


WEBAPP_BASE_URL = settings.webapp_base_url   # адрес вида https://teatr-svoi.online


TOOL_API_TOKEN = settings.tool_api_token


ADMIN_CHAT_ID = settings.admin_chat_id


# Часовой пояс студии: гейтвей своих дат не считает, поэтому смещение берём из
# окружения (на проде TZ_OFFSET_HOURS=4), а не зашиваем в код.
LOCAL_TZ = settings.local_tz

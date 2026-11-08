"""Ошибки tool-api: код для ветвления и текст для человека."""

from __future__ import annotations

import re

import httpx
import texts
from aiogram.exceptions import TelegramAPIError

_CODE_ONLY = re.compile(r"^[a-z][a-z0-9_]+$")


def error_code(res: dict) -> str:
    """Код ошибки из ответа tool-api: новый конверт (`code`) или старые поля.

    Пока не все маршруты переведены на конверт, порядок такой: code → error → detail.
    Ветвиться в логике нужно по этому значению, а не по тексту для человека.
    """
    return str(res.get("code") or res.get("error") or res.get("detail") or "")


def error_text(res: dict) -> str:
    """Текст для человека: `detail`, если он не голый код; иначе `title`/`message`."""
    detail = str(res.get("detail") or "").strip()
    if detail and not _CODE_ONLY.fullmatch(detail):
        return detail
    return str(res.get("title") or res.get("message") or detail or texts.NO_ANSWER)


# Ошибки внешних вызовов: ловим только их, всё остальное — поломка кода (R-5).
# TelegramAPIError — база aiogram для отказа Bot API и сетевых сбоев (3.x).
TELEGRAM_ERRORS = (TelegramAPIError,)
HTTP_ERRORS = (httpx.HTTPError,)

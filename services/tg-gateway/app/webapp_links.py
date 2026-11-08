"""
Ссылки на Mini App: один адрес из окружения (M-1, docs/infrastructure/75 §5.2).

Правило проекта: `http://` в проде — ошибка конфигурации, а не «пока сойдёт».
Telegram не откроет Mini App по HTTP, и человек увидит пустой экран без объяснений.
Поэтому:
  * пусто — кнопку не показываем вовсе (локальная разработка, обкатка без домена);
  * `https://…` — показываем, хвостовой слэш срезаем, чтобы в адресе не было `//`;
  * `http://…` — считаем адрес непригодным и молчим: лучше без кнопки, чем сломанная.

Модуль знает только aiogram-типы, поэтому правило «кнопка или ничего» проверяется
тестом без запуска бота.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

SCHEME = "https://"


def base_url(value: str | None) -> str:
    """Нормализованный базовый адрес Mini App или пустая строка, если показывать нечего."""
    text = (value or "").strip()
    if not text.lower().startswith(SCHEME):
        return ""
    return text.rstrip("/")


def page_url(path: str, value: str | None) -> str | None:
    """Адрес страницы Mini App или None, если базовый адрес непригоден."""
    base = base_url(value)
    if not base:
        return None
    return f"{base}/{path.lstrip('/')}"


def keyboard(text: str, path: str, value: str | None) -> InlineKeyboardMarkup | None:
    """Клавиатура с одной кнопкой Mini App. None — если базовый адрес не задан."""
    url = page_url(path, value)
    if url is None:
        return None
    button = InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def hint(path: str, value: str | None) -> str | None:
    """Строка «открыть в приложении» для текста, если кнопку показать не можем."""
    base = base_url(value)
    if not base:
        return None
    return f"{base}/{path.lstrip('/')}"

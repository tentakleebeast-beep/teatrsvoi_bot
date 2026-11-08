"""tg-gateway: точка входа — сборка Dispatcher, middleware, polling.

Обработчики живут в роутерах по домену (`routers/`), тексты — в `texts.py`,
клавиатуры — в `keyboards.py`, вызовы tool-api и права — в `core.py`.
Порядок подключения роутеров повторяет порядок объявления обработчиков в
прежнем одном файле: он определяет, какой обработчик получит апдейт первым.
"""

from __future__ import annotations

import asyncio
import functools

import commands
import runtime
import single_instance
import texts
from aiogram import Bot, Dispatcher
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from routers import (
    admin,
    agent,
    assign,
    availability,
    budget,
    confirm,
    events,
    fallback,
    feedback,
    files,
    intro,
    menu,
    notes,
    profile,
)
from webapp_links import page_url

from config import TOKEN, TOOL_API_URL, WEBAPP_BASE_URL, log


class HtmlFallbackMiddleware(BaseRequestMiddleware):
    """Страховка: если текст не разбирается как HTML, отправляем его как обычный.

    Причина — данные из файлов/БД и подсказки вида «/grant ИМЯ РОЛЬ» попадают
    в сообщение с parse_mode=HTML и Telegram отвечает «can't parse entities».
    Без этой страховки пользователь не получает ничего, а в лог падает трейсбек.
    """

    async def __call__(self, make_request, bot, method):
        from aiogram.exceptions import TelegramBadRequest

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if "can't parse entities" not in str(exc) or "parse_mode" not in type(method).model_fields:
                raise
            log.warning("HTML не разобрался, повтор без разметки: %s", str(exc)[:160])
            method.parse_mode = None
            return await make_request(bot, method)


@functools.cache
def build_dispatcher() -> Dispatcher:
    """Собрать Dispatcher: сначала меню и формы, последним — перехватчик сбоев.

    Роутеры — модульные объекты, подключить их можно только к одному Dispatcher
    за процесс; поэтому сборка кэшируется (тесты и повторный старт получают тот же).
    """
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(
        menu.router,
        availability.router,
        budget.router,
        confirm.router,
        feedback.router,
        profile.router,
        admin.router,
        assign.router,
        events.router,
        files.router,
        agent.router,
        intro.router,
        notes.router,
        fallback.router,
    )
    return dp


async def main() -> None:
    # Ш-28: один токен — один поллер. Второй экземпляр не должен даже подключиться
    # к Telegram: иначе он забирает апдейты у первого (409 Conflict, потерянные
    # сообщения). launchd/systemd держат один процесс, ручной запуск — нет.
    try:
        lock = single_instance.acquire()
    except single_instance.AlreadyRunning as exc:
        log.error("%s", exc)
        raise SystemExit(str(exc)) from None
    log.info("единственный экземпляр: замок %s", lock)
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN не задан")
    # Разметка боту не нужна (в текстах нет ни одного <b>/<i>), а HTML-режим ломал
    # сообщения, где встречается пользовательский текст с «<» или «&».
    runtime._bot = Bot(token=TOKEN)
    runtime._bot.session.middleware(HtmlFallbackMiddleware())
    dp = build_dispatcher()
    # Кнопка меню в чате ведёт в хаб, а не сразу в неделю: человек должен видеть
    # все разделы, а не один экран по умолчанию (замечание 30.10.2026).
    menu_url = page_url("/webapp/home", WEBAPP_BASE_URL)
    if menu_url:
        await runtime._bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text=texts.MENU_BUTTON, web_app=WebAppInfo(url=menu_url)))
        log.info("меню бота: Mini App %s", menu_url)
    # Меню команд ставит код, а не человек: список один (`commands.py`), и по нему же
    # тест сверяет подсказки. Иначе в меню `/note`, а в совете `/заметка`.
    # Отказ Telegram не роняет старт: бот работает и без меню, причина — в логе.
    menu_commands = commands.menu_commands()
    try:
        await runtime._bot.set_my_commands(
            [BotCommand(command=item.name, description=item.description)
             for item in menu_commands])
        log.info("меню команд: %s", ", ".join(item.name for item in menu_commands))
    except TelegramAPIError as exc:
        log.warning("меню команд не поставлено: %s", exc)
    log.info("gateway started, tool-api=%s", TOOL_API_URL)
    await dp.start_polling(runtime._bot)


if __name__ == "__main__":
    asyncio.run(main())

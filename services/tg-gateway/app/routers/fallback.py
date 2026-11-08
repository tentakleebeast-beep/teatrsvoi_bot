"""Последний роутер: ответ на сбой обработчика и на непонятый текст."""

from __future__ import annotations

import errors
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.types.error_event import ErrorEvent

from config import log

router = Router()


@router.errors()
async def on_handler_error(event: ErrorEvent) -> bool:
    """Ни один сбой обработчика не должен оставлять человека без ответа.

    Найдено smoke-тестом 27.10: битая/stale кнопка (`evpick:7`) роняла ValueError
    и пользователь не получал ничего. Теперь логируем и отвечаем по-русски.
    """
    log.exception("Ошибка обработчика: %s", event.exception)
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(
                "Не получилось. Откройте меню заново", show_alert=True)
        elif update.message is not None:
            await update.message.answer(
                "Не получилось обработать. Откройте меню заново.")
    except errors.TELEGRAM_ERRORS:                # ответ уже не доставить, главное не упасть
        log.warning("не удалось сообщить об ошибке пользователю", exc_info=True)
    return True


@router.message(StateFilter("*"), F.text & ~F.text.startswith("/"))
async def state_fallback_text(message: Message, state: FSMContext) -> None:
    """Ш-52 п.3: любое состояние имеет выход, и бот не молчит.

    Сюда попадает свободный текст в состоянии, у которого нет своего разбора
    (например, подтверждение формы). Раньше такое сообщение просто терялось.
    """
    await state.clear()
    await message.answer(
        "Не понял, попробуйте ещё раз или обратитесь к разработчику.\n"
        "Режим ввода закрыт — начать заново можно кнопкой в меню или /start.")

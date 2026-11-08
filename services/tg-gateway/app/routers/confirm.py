"""Подтверждение предложений агента кнопками (A-3, docs/development/108 §8).

Карточку рисует меню (`routers/menu.py`), а нажатия разбирает этот модуль. Кнопка
несёт одноразовый токен, а не аргументы операции: сумму, категорию и дату знает
сервер, и подменить их из чата нельзя.

  * «Подтвердить» → `/tools/agent/confirm` → обычный `/tools/budget/commit`
    с тем же правом `budget.write`, что у формы;
  * «Отмена» → `/tools/agent/cancel`: ничего не записано, отказ в `audit_event`.

Повторное нажатие и токен старше 5 минут — отказ без дубля; текст берётся из
`texts.py`, чтобы формулировка была одной на все экраны.
"""

from __future__ import annotations

import core
import errors
import texts
from aiogram import F, Router
from aiogram.types import CallbackQuery

router = Router()

#: Коды отказа, у которых свой человеческий текст. Остальное — общая фраза:
#: человеку не нужно знать про `token_foreign`, ему нужно знать, что делать.
REFUSAL_TEXTS = {
    "token_expired": texts.AGENT_EXPIRED,
    "token_used": texts.AGENT_ALREADY,
    "token_cancelled": texts.AGENT_NOTHING_WRITTEN,
    "commit_failed": texts.AGENT_WRITE_FAILED,
}


@router.callback_query(F.data.startswith("agc:"))
async def agent_confirm_cb(cb: CallbackQuery) -> None:
    """Разобрать «Подтвердить» / «Отмена» по одноразовому токену карточки."""
    if cb.message is None:
        await cb.answer(texts.MESSAGE_UNAVAILABLE, show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("ok", "no") or not parts[2]:
        await cb.answer(texts.STALE_BUTTON, show_alert=True)
        return
    verb, token = parts[1], parts[2]
    chat_id = cb.message.chat.id
    await cb.answer()

    if verb == "no":
        result = await core.agent_cancel(chat_id, token)
        if result.get("ok"):
            await cb.message.edit_text(texts.AGENT_NOTHING_WRITTEN)
            return
        await cb.message.edit_text(REFUSAL_TEXTS.get(
            errors.error_code(result), texts.AGENT_CONFIRM_FAILED))
        return

    result = await core.agent_confirm(chat_id, token)
    if result.get("ok"):
        await cb.message.edit_text(
            texts.agent_written(result.get("proposal") or {},
                                bool(result.get("deduped")), result))
        return
    reason = REFUSAL_TEXTS.get(errors.error_code(result), texts.AGENT_CONFIRM_FAILED)
    await cb.message.edit_text(reason)


@router.callback_query(F.data.startswith("agp:"))
async def agent_plan_cb(cb: CallbackQuery) -> None:
    """«Выполнить план» / «Отмена»: один токен на все шаги плана.

    Частичный успех показывается как есть: `agent_plan_result` называет, сколько
    шагов прошло и на каком код остановился (карта «Озадачить», п.4).
    """
    if cb.message is None:
        await cb.answer(texts.MESSAGE_UNAVAILABLE, show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("ok", "no") or not parts[2]:
        await cb.answer(texts.STALE_BUTTON, show_alert=True)
        return
    verb, token = parts[1], parts[2]
    chat_id = cb.message.chat.id
    await cb.answer()

    if verb == "no":
        result = await core.agent_cancel(chat_id, token)
        if result.get("ok"):
            await cb.message.edit_text(texts.AGENT_NOTHING_WRITTEN)
            return
        await cb.message.edit_text(REFUSAL_TEXTS.get(
            errors.error_code(result), texts.AGENT_CONFIRM_FAILED))
        return

    result = await core.agent_plan_confirm(chat_id, token)
    if result.get("results") is not None:
        await cb.message.edit_text(texts.agent_plan_result(result))
        return
    await cb.message.edit_text(REFUSAL_TEXTS.get(
        errors.error_code(result), texts.AGENT_CONFIRM_FAILED))

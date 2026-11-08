"""Роутер обратной связи: сообщения людей и решения админа."""

from __future__ import annotations

import core
import errors
import keyboards
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from states import Feedback

router = Router()


@router.callback_query(F.data.startswith("fb:"))
async def fb_kind(cb: CallbackQuery, state: FSMContext) -> None:
    """Выбор типа обращения из кнопок меню."""
    kind = cb.data.split(":", 1)[1]
    await state.clear()
    await state.update_data(fb_kind=kind)
    await state.set_state(Feedback.await_text)
    await cb.message.edit_text(
        texts.FEEDBACK_KINDS.get(kind, "Обращение") + ". Напишите подробнее одним сообщением.")
    await cb.answer()


@router.message(Feedback.await_text, F.text, ~F.text.startswith("/"))
async def fb_text(message: Message, state: FSMContext) -> None:
    if message.text.strip() in keyboards.MENU_SURFACE:
        await state.clear()
        await message.answer("Отменил. Обращение не отправлено.")
        return
    data = await state.get_data()
    kind = data.get("fb_kind", "bug")
    await state.clear()
    await send_feedback(message, kind, message.text)


async def send_feedback(message: Message, kind: str, text: str) -> None:
    text = text.strip()
    if len(text) < 3:
        await message.answer("Слишком коротко — напишите пару слов.")
        return
    trimmed = len(text) > 4000
    text = text[:4000]
    res = await core.api("/tools/feedback",
                    {"kind": kind, "text": text,
                     "context": {"screen": "chat", "text": text[:120]}},
                    message.chat.id)
    if not res.get("ok"):
        await message.answer("Не получилось отправить: " + errors.error_text(res))
        return
    note = " (обрезал до 4000 знаков)" if trimmed else ""
    await message.answer(
        texts.PREFIX_WRITTEN + res.get("kind_title", "обращение") + note + ".\n"
        "Организаторы увидят сообщение, ответ придёт сюда.")


@router.message(Command("feedback"))
async def feedback_triage(message: Message, state: FSMContext) -> None:
    """/feedback — очередь обращений для организаторов."""
    await state.clear()
    res = await core.api("/tools/feedback?status=new", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Очередь обращений доступна организаторам.")
        return
    items = res.get("data") or []
    if not items:
        await message.answer("Новых обращений нет.")
        return
    for item in items[:5]:
        text = ("№" + str(item["id"]) + " · " + (item.get("author") or texts.ROLE_PARTICIPANT) + "\n"
                + item["text"][:400])
        await message.answer(
            text, parse_mode=None,
            reply_markup=keyboards.kb([[("В работу", f"fbd:{item['id']}:seen"),
                              ("Сделано", f"fbd:{item['id']}:done")],
                             [(texts.BTN_REJECT, f"fbd:{item['id']}:rejected")]]))


@router.callback_query(F.data.startswith("fbq:"))
async def fb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    """
    Подтверждение обращения из свободного текста.

    Автоматически не записываем: похожая формулировка может оказаться обычным
    вопросом («не работает расписание» — это может быть и жалоба, и просьба помочь).
    """
    data = await state.get_data()
    text = data.get("fb_pending") or ""
    kind = data.get("fb_kind") or "bug"
    await state.update_data(fb_pending=None)

    if cb.data == "fbq:no":
        await cb.message.edit_text(
            "Понял, это не обращение. Сформулируйте вопрос — отвечу по существу.")
        await cb.answer()
        return

    await cb.message.edit_text("Записываю обращение.")
    await send_feedback(cb.message, kind, text)
    await cb.answer("Отправлено")


@router.callback_query(F.data.startswith("fbd:"))
async def feedback_decide(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("seen", "done", "rejected"):
        await cb.answer(texts.STALE_BUTTON, show_alert=True)
        return
    _, feedback_id, status = parts
    titles = {"seen": texts.STATUS_IN_PROGRESS, "done": texts.STATUS_DONE, "rejected": texts.STATUS_REJECTED}
    res = await core.api("/tools/feedback/decide",
                    {"feedback_id": int(feedback_id), "status": status},
                    cb.message.chat.id)
    if res.get("ok"):
        await cb.message.edit_text("Обращение №" + feedback_id + ": "
                                   + titles.get(status, status)
                                   + ". Автор получил уведомление.", parse_mode=None)
        await cb.answer("Отмечено")
    else:
        await cb.answer("Не получилось", show_alert=True)

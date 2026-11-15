"""Опросы в чате: шаг ответа, «Назад», «Пропустить», свободный ввод (E8).

Спецификация — `docs/development/162-bot-polls.md` §14.4 и §5. Черновик прохождения
живёт в БД (`poll_run`), состояние бота нужно ровно для одного: поймать следующее
сообщение как свободный ответ (`PollAnswer.await_text`). Перезапуск gateway прогресс
не теряет: «Продолжить» читает `poll_run.position` (AC-8).

Шаги идут мимо очереди уведомлений: человек только что нажал кнопку, значит бот
отвечает на действие, а не пишет неожиданно — тихие часы и лимит 3/час здесь не
действуют (D5, AC-13). Первый вопрос приходит push-ом через `notification`
с `payload.markup` (`scripts/send_notifications.py`).

Кнопки собирает `app/poll_ui.py` — чистый модуль, где длина `callback_data`
проверяется тестом, а не Telegram.
"""

from __future__ import annotations

import poll_ui
import texts
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from core import api
from keyboards import kb
from states import PollAnswer

from config import log

router = Router(name="polls")


def _markup(step: dict) -> InlineKeyboardMarkup:
    return kb(poll_ui.keyboard_rows(step))


async def _send_step(message: Message, step: dict) -> None:
    """Один вопрос — одно сообщение: шаг вперёд правит его, а не шлёт новое (D6)."""
    if step.get("closed"):
        await message.edit_text(poll_ui.closed_text(step))
        return
    if step.get("done"):
        await message.edit_text(poll_ui.done_text(step))
        return
    await message.edit_text(poll_ui.question_text(step), reply_markup=_markup(step))


async def _render(callback: CallbackQuery, payload: dict) -> None:
    """Показать ответ сервера на действие: закрыт, пройден или следующий вопрос."""
    if not payload.get("ok"):
        await callback.message.answer(payload.get("detail") or texts.POLL_FAILED)
        return
    if payload.get("closed"):
        await _send_step(callback.message, payload["step"])
        return
    if payload.get("done"):
        await _send_step(callback.message, payload["step"])
        return
    if payload.get("retry"):
        await callback.message.answer(payload.get("message") or texts.POLL_NUMBER_ERROR)
        return
    await _send_step(callback.message, payload["step"])


async def _answer(callback: CallbackQuery, run_id: int, position: int | None,
                  action: str, code: str | None = None) -> dict:
    body = {"run_id": run_id, "action": action}
    if position is not None:
        body["position"] = position
    if code:
        body["code"] = code
    return await api("/tools/polls/answer", body, callback.message.chat.id)


@router.callback_query(F.data.startswith("p:"))
async def poll_step(callback: CallbackQuery, state: FSMContext) -> None:
    """Нажатие на кнопку шага: выбор, «Дальше», «Да/Нет», «Назад», «Пропустить»."""
    parts = poll_ui.parse(callback.data or "")
    await callback.answer()
    if parts is None:
        return
    action = parts["action"]
    if action == "q":
        # «Продолжить» из меню: прогресс читается из БД, состояние не нужно.
        result = await api("/tools/polls/step", None, callback.message.chat.id,
                           method="GET", timeout=25)
        if not result.get("ok"):
            await callback.message.answer(result.get("detail") or texts.POLL_FAILED)
            return
        step = result["step"]
        if step.get("closed"):
            await state.clear()
            await callback.message.answer(poll_ui.closed_text(step))
            return
        if step.get("done"):
            await state.clear()
            await callback.message.answer(poll_ui.done_text(step))
            return
        await callback.message.answer(poll_ui.question_text(step), reply_markup=_markup(step))
        return
    if action == "f":
        # «Другое — напишу» / «Ответить сообщением»: следующее сообщение человека
        # станет ответом (D4). Тот же вход несёт кнопка приглашения из очереди.
        await state.set_state(PollAnswer.await_text)
        await state.update_data(run_id=parts["run_id"], position=parts["position"])
        await callback.message.answer(texts.POLL_WRITE_ANSWER)
        return
    payload = await _answer(callback, parts["run_id"], parts["position"], action,
                            parts.get("code"))
    await _render(callback, payload)


@router.message(PollAnswer.await_text, F.text)
async def poll_free_text(message: Message, state: FSMContext) -> None:
    """Свободный ответ или число. Число не разобралось — повторяем, не теряя вопрос."""
    data = await state.get_data()
    result = await api("/tools/polls/answer",
                       {"run_id": data.get("run_id"), "action": "text",
                        "position": data.get("position"), "text": message.text},
                       message.chat.id)
    if result.get("retry"):
        await message.answer(result.get("message") or texts.POLL_NUMBER_ERROR)
        return
    await state.clear()
    if not result.get("ok"):
        await message.answer(result.get("detail") or texts.POLL_FAILED)
        return
    step = result.get("step") or {}
    if result.get("closed"):
        await message.answer(poll_ui.closed_text(step))
        return
    if result.get("done"):
        await message.answer(poll_ui.done_text(step))
        return
    await message.answer(poll_ui.question_text(step), reply_markup=_markup(step))


async def show_runs(message: Message, state: FSMContext) -> None:
    """Пункт меню «Опросы»: незаконченные прохождения и «Продолжить» (D4, §5.3).

    Подпись кнопки статическая (`p:q:<run>` в данных, не в тексте): сторож
    `button_scan` не переваривает вычисляемые подписи, а прогресс живёт в сообщении.
    """
    await state.clear()
    result = await api("/tools/polls/mine", None, message.chat.id, method="GET")
    if not result.get("ok"):
        await message.answer(result.get("detail") or texts.POLL_FAILED)
        return
    runs = result.get("runs") or []
    if not runs:
        await message.answer(texts.POLL_NO_RUNS)
        return
    rows = [[(texts.BTN_CONTINUE, poll_ui.callback("q", run["run_id"]))] for run in runs]
    lines = ["Ваши опросы:"]
    for run in runs:
        lines.append(f"— {run['title']}: вопрос {run['position']} из {run['total']}")
    await message.answer("\n".join(lines), reply_markup=kb(rows))
    log.info("опросы: у %s незаконченных %s", message.chat.id, len(runs))

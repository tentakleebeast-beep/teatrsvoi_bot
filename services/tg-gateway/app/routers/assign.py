"""Роутер назначения событий: «назначь репетицию …».

Диалог, а не одна реплика. Живой разговор 05.11.2026 («поставь встречу с Марком и
Машей» → «завтра» → «с 15-00 до 18-00») распадался на три независимых разбора:
«завтра» уходило в расписание, а интервал времени — в черновик расхода на 15 ₽.
Поэтому назначение помнит уже сказанное: фраза складывается в состояние
`AssignDraft`, а какого поля не хватает — говорит код ошибки tool-api
(`no_kind`, `no_date`, `no_time`). Разбор текста живёт на сервере и здесь
не повторяется: два разбора разошлись бы.
"""

from __future__ import annotations

import re

import core
import errors
import texts
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from routing import ASSIGN_Q, BUDGET_WORDS, MONEY_Q, SCHEDULE_Q
from states import AssignDraft

import routers.events as routers_events

router = Router()


ASSIGN_Q_PATTERN = ASSIGN_Q          # шаблон «назначь …» для фильтра aiogram

#: Код ошибки tool-api → состояние ожидания. Заодно это и список того, что диалог
#: умеет спрашивать: остальные ошибки (`bad_time`, `venue_busy`) — не «не хватает
#: данных», а отказ, его человеку показывают сразу.
MISSING_STATES: dict[str, State] = {
    "no_kind": AssignDraft.await_kind,
    "bad_date": AssignDraft.await_date,
    "no_date": AssignDraft.await_date,
    "no_time": AssignDraft.await_time,
}

#: Состояния диалога назначения: по ним menu.on_text отличает ответ боту от новой фразы.
DRAFT_STATES = frozenset(state.state for state in MISSING_STATES.values())

#: Явно другое намерение внутри диалога: черновик снимается, реплика идёт обычным
#: путём. Признаки — слова, а не «в строке есть цифра»: «с 15-00 до 18-00» это
#: продолжение назначения, и цифры в нём ничего не решают.
OTHER_INTENT = re.compile(
    rf"(?:{MONEY_Q})|(?:{BUDGET_WORDS})|(?:{SCHEDULE_Q})|(?:{ASSIGN_Q})", re.IGNORECASE)

#: «Отмена» словом — то же, что кнопка «Отмена».
CANCEL_WORDS = re.compile(r"^(?:отмена|отмени|отменить|стоп|хватит|не\s+надо|нет)\b",
                          re.IGNORECASE)

#: «заметка: …» — не ответ про день, а другое действие чата (FR-F7).
NOTE_START = re.compile(r"^заметк", re.IGNORECASE)

#: Черновик в состоянии: ключ `ad`, поле `text` — то, что человек уже сказал.
DRAFT_KEY = "ad"


def draft_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка «Отмена» под вопросом: выйти из диалога можно одним нажатием."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="evdraft:no")]])


def missing_code(res: dict) -> str | None:
    """Какого поля не хватает по ответу tool-api (None — данные полные)."""
    for item in res.get("errors") or []:
        if item.get("code") in MISSING_STATES:
            return item["code"]
    return None


def draft_phrase(data: dict) -> str:
    """Фраза, накопленная диалогом."""
    return str((data.get(DRAFT_KEY) or {}).get("text") or "")


async def _ask(message: Message, state: FSMContext | None, code: str, text: str) -> None:
    """Спросить недостающее и запомнить сказанное: остальное не теряется."""
    if state is not None:
        await state.update_data(**{DRAFT_KEY: {"text": text}})
        await state.set_state(MISSING_STATES[code])
    await message.answer(texts.ASSIGN_ASK.get(code, texts.ASSIGN_ASK["no_date"]),
                         reply_markup=draft_cancel_kb())


def card_text(card: dict) -> str:
    """Карточка события: детали, без которых подтверждать нечего."""
    lines = [f"{card['kind_title']} — {card['title']}", "",
             f"{card['weekday']}, {card['date'][8:10]}.{card['date'][5:7]}, "
             f"{card['from_hour']:02d}:00–{card['to_hour']:02d}:00",
             f"Помещение: {card['venue_name']}"]
    if card["participants"]:
        lines.append("Участники: " + ", ".join(p["name"] for p in card["participants"]))
    for director in card.get("directors") or []:
        presence = "присутствует" if director.get("will_attend") else "не присутствует"
        lines.append(f"Режиссёр: {director['name']} — {presence} (по возможности)")
    return "\n".join(lines)


def card_notes(res: dict) -> list[str]:
    """Предупреждения и запреты под карточкой: занятость людей и помещение."""
    lines: list[str] = []
    for warning in res.get("warnings") or []:
        lines.extend(["", "Внимание: " + warning["text"]])
    for error in res.get("errors") or []:
        lines.extend(["", "Нельзя назначить: " + error["text"]])
    return lines


def card_keyboard(res: dict, card: dict) -> InlineKeyboardMarkup | None:
    """Кнопки карточки: выбор тёзки, подтверждение или ничего (помещение занято)."""
    ambiguity = card.get("ambiguity") or {}
    if ambiguity:
        # «у нас три Вити» — показываем варианты, а не угадываем
        rows = []
        for _, options in ambiguity.items():
            for option in options:
                rows.append([InlineKeyboardButton(
                    text=option["name"],
                    callback_data=f"evpick:{res['proposal_id']}:{option['id']}")])
        rows.append([InlineKeyboardButton(text=texts.BTN_CANCEL,
                                          callback_data=f"evno:{res['proposal_id']}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if res.get("can_assign"):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Назначить",
                                 callback_data=f"evok:{res['proposal_id']}"),
            InlineKeyboardButton(text=texts.BTN_CANCEL,
                                 callback_data=f"evno:{res['proposal_id']}"),
        ]])
    return None


async def _show_card(message: Message, res: dict) -> None:
    card = res["card"]
    text = card_text(card) + "\n".join(card_notes(res))
    await message.answer(text[:3800], reply_markup=card_keyboard(res, card))


async def _after_preview(message: Message, state: FSMContext | None, res: dict,
                         text: str) -> None:
    """Ответ tool-api → либо вопрос про недостающее, либо карточка.

    Один разбор на все исходы: реплика целиком и реплика с уточнением отличаются
    только накопленным текстом, а не логикой.
    """
    code = missing_code(res)
    if code:
        await _ask(message, state, code, text)
        return
    if state is not None:
        await state.clear()
    if not res.get("card"):
        reasons = "; ".join(item["text"] for item in res.get("errors") or [])
        await message.answer(reasons or "Не понял, что назначить. Пример: "
                             "«назначь репетицию с Витей в четверг в 20:00»")
        return
    await _show_card(message, res)


async def preview(message: Message, text: str) -> dict:
    """Предпросмотр события по тексту: заявка сохраняется для подтверждения."""
    return await core.api("/tools/events/preview",
                          {"text": text, "persist": True}, message.chat.id)


@router.message(F.text.regexp(ASSIGN_Q_PATTERN, flags=re.IGNORECASE))
async def assign_start(message: Message, state: FSMContext | None = None) -> None:
    """
    «Назначь репетицию с Витей и Людой в четверг в 20:00» — предпросмотр
    и подтверждение кнопкой (docs/architecture/51). Занятость людей только предупреждает,
    занятое помещение блокирует. Если чего-то не хватает, начинается диалог:
    спрашиваем только недостающее.
    """
    text = (message.text or "").strip()
    if state is not None:
        await state.clear()
    await _after_preview(message, state, await preview(message, text), text)


async def assign_continue(message: Message, state: FSMContext, text: str) -> bool:
    """Реплика внутри диалога назначения: дописать её к сказанному и продолжить.

    True — реплика обработана здесь (вопрос или карточка). False — это явно другое
    намерение: черновик снят, и вызывающий обрабатывает текст обычным путём.
    """
    said = draft_phrase(await state.get_data())
    if CANCEL_WORDS.search(text.strip()):
        await state.clear()
        await message.answer(texts.ASSIGN_CANCEL_DONE)
        return True
    if OTHER_INTENT.search(text) or NOTE_START.search(text.strip()):
        await state.clear()
        return False
    if not said:
        # состояние без черновика (перезапуск процесса, память FSM): начинаем заново
        await state.clear()
        return False
    merged = f"{said} {text}".strip()
    await _after_preview(message, state, await preview(message, merged), merged)
    return True


@router.callback_query(F.data == "evdraft:no")
async def draft_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """«Отмена» посреди уточнения: состояние снимается, ничего не записано."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(texts.ASSIGN_CANCEL_DONE)


@router.callback_query(F.data.startswith("evpick:"))
async def assign_pick(callback: CallbackQuery) -> None:
    """Организатор выбрал, кто именно из тёзок имеется в виду."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts[1:]):
        # старая или битая кнопка: раньше это давало ValueError и молчание
        await callback.answer("Кнопка устарела, начните назначение заново", show_alert=True)
        return
    _, proposal_id, participant_id = parts
    await callback.answer("Уточняю")
    res = await core.api("/tools/events/choose",
                    {"proposal_id": int(proposal_id), "participant_id": int(participant_id)},
                    callback.message.chat.id)
    if not res.get("card"):
        await callback.message.answer("Не получилось уточнить состав. Попробуйте ещё раз.")
        return

    await _show_card(callback.message, res)


@router.callback_query(F.data.startswith("evok:"))
async def assign_confirm(callback: CallbackQuery) -> None:
    proposal_id = int(callback.data.split(":", 1)[1])
    await callback.answer("Записываю")
    res = await core.api("/tools/events/assign", {"proposal_id": proposal_id},
                    callback.message.chat.id)
    if not res.get("ok"):
        detail = errors.error_text(res)
        text = {"slot_taken": "Это время уже занято — найдите другое окно.",
                "already_assigned": "Это событие уже назначено."}.get(detail,
                                                                     f"Не получилось: {detail}")
        await callback.message.answer(text)
        return

    card = res["card"]
    lines = [f"Назначено: {card['kind_title']} — {card['title']}",
             f"{card['weekday']}, {card['date'][8:10]}.{card['date'][5:7]}, "
             f"{card['from_hour']:02d}:00–{card['to_hour']:02d}:00",
             f"{card['venue_name']}"]
    if res.get("in_calendar"):
        lines.append("Событие записано в календарь.")
    else:
        lines.append("Календарь недоступен — событие только в расписании.")
    if res.get("notified"):
        lines.append(f"Уведомления поставлены: {res['notified']}.")
    await callback.message.answer("\n".join(lines),
                                  reply_markup=routers_events.event_markup(res["event_id"]))


@router.callback_query(F.data.startswith("evno:"))
async def assign_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer(texts.ASSIGN_CANCEL_DONE)

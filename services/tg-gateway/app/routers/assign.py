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
DRAFT_STATES = frozenset(state.state for state in MISSING_STATES.values()) | {
    AssignDraft.await_window.state}

#: Последняя показанная карточка и личные окна — в состоянии, а не в callback:
#: кнопка несёт только id человека, а окно надо запомнить до подтверждения.
PREVIEW_KEY = "ap"
WINDOW_PERSON_KEY = "awho"
WINDOW_KEY = "awin"

#: «19:30–21:00», «19.30-21.00», «с 19:30 до 21:00» — время начала и конца.
TIME_PAIR = re.compile(r"(\d{1,2})[:.](\d{2})\D+(\d{1,2})[:.](\d{2})")

#: «весь», «всё событие», «-» — личного окна нет: человек задействован весь интервал.
WHOLE_WORDS = re.compile(r"^(?:весь|всё|все|всё событие|-)$", re.IGNORECASE)

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


def _person_text(person: dict) -> str:
    """Имя и личное время участия, если оно задано (V-134)."""
    name = person.get("name") or ""
    time_from, time_to = person.get("time_from"), person.get("time_to")
    if not time_from and not time_to:
        return name
    return f"{name} ({time_from or '—'}–{time_to or '—'})"


def card_text(card: dict) -> str:
    """Карточка события: детали, без которых подтверждать нечего."""
    lines = [f"{card['kind_title']} — {card['title']}", "",
             f"{card['weekday']}, {card['date'][8:10]}.{card['date'][5:7]}, "
             f"{card['from_hour']:02d}:00–{card['to_hour']:02d}:00",
             f"Помещение: {card['venue_name']}"]
    if card["participants"]:
        lines.append("Участники: " + ", ".join(_person_text(p) for p in card["participants"]))
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
    """Кнопки карточки: выбор тёзки, подтверждение или ничего (помещение занято).

    Перед подтверждением можно задать личное время участия (V-134): кнопка на
    каждого участника, потому что имя в тексте пришлось бы разбирать заново, а
    «Витя» и «Виктор» — это один человек только в карточке.
    """
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
        rows = [[InlineKeyboardButton(
            text="Время: " + person["name"],
            callback_data=f"evwin:{res['proposal_id']}:{person['id']}")]
            for person in card.get("participants") or [] if person.get("id") is not None]
        rows.append([
            InlineKeyboardButton(text="Назначить",
                                 callback_data=f"evok:{res['proposal_id']}"),
            InlineKeyboardButton(text=texts.BTN_CANCEL,
                                 callback_data=f"evno:{res['proposal_id']}"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return None


async def _show_card(message: Message, res: dict, state: FSMContext | None = None) -> None:
    card = res["card"]
    if state is not None:
        await state.update_data(**{PREVIEW_KEY: res})
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
        # Личные окна, уже заданные кнопкой «Время: …», переживают повторный
        # предпросмотр: иначе уточнение тёзки молча стирало бы их (V-134).
        data = await state.get_data()
        keep = {key: data[key] for key in (WINDOW_KEY,) if key in data}
        await state.clear()
        if keep:
            await state.update_data(**keep)
    if not res.get("card"):
        reasons = "; ".join(item["text"] for item in res.get("errors") or [])
        await message.answer(reasons or "Не понял, что назначить. Пример: "
                             "«назначь репетицию с Витей в четверг в 20:00»")
        return
    await _show_card(message, res, state)


def _windows_payload(data: dict) -> list[dict]:
    """Личные окна из состояния → тело запроса `assign` (V-134)."""
    windows = data.get(WINDOW_KEY) or {}
    return [{"participant_id": int(pid),
             "time_from": value.get("time_from"),
             "time_to": value.get("time_to")}
            for pid, value in sorted(windows.items(), key=lambda pair: int(pair[0]))]


def _clock_pair(match: re.Match) -> tuple[str, str] | None:
    """«19:30–21:00» → («19:30», «21:00»); кривые часы и минуты — None."""
    first_hour, first_minute, second_hour, second_minute = (int(part) for part in match.groups())
    if first_hour > 23 or second_hour > 23 or first_minute > 59 or second_minute > 59:
        return None
    if (second_hour, second_minute) <= (first_hour, first_minute):
        return None
    return (f"{first_hour:02d}:{first_minute:02d}", f"{second_hour:02d}:{second_minute:02d}")


async def _window_reply(message: Message, state: FSMContext, text: str) -> bool:
    """Ответ на «для кого время»: «19:30–21:00» или «весь» (V-134).

    Минуты сохраняются как есть: актёр просит «со скольки задействован», и
    округлить 19:30 до 19:00 значило бы показать ему чужое время.
    """
    data = await state.get_data()
    person = data.get(WINDOW_PERSON_KEY) or {}
    if not person:
        await state.clear()
        await message.answer("Не понял, для кого время участия. Начните назначение заново.")
        return True
    windows = dict(data.get(WINDOW_KEY) or {})
    key = str(person["id"])
    if WHOLE_WORDS.match(text.strip()):
        windows.pop(key, None)
        await state.update_data(**{WINDOW_KEY: windows})
        await message.answer(f"{person.get('name')}: весь интервал события. "
                             "Можно задать время другому или нажать «Назначить».")
        return True
    match = TIME_PAIR.search(text)
    pair = _clock_pair(match) if match else None
    if pair is None:
        await message.answer("Не понял время. Например: 19:30–21:00. "
                             "Пусто — весь интервал: напишите «весь».")
        return True
    time_from, time_to = pair
    windows[key] = {"time_from": time_from, "time_to": time_to}
    await state.update_data(**{WINDOW_KEY: windows})

    stored = data.get(PREVIEW_KEY) or {}
    card = stored.get("card")
    if card:
        # Карточка показывается заново: организатор видит окно там, где оно и
        # будет после назначения, а не только в ответе бота.
        for participant in card.get("participants") or []:
            if int(participant["id"]) == int(person["id"]):
                participant["time_from"], participant["time_to"] = time_from, time_to
        await state.update_data(**{PREVIEW_KEY: stored})
        await _show_card(message, stored, state)
    else:
        await message.answer(f"{person.get('name')}: {time_from}–{time_to}. "
                             "Нажмите «Назначить», когда всё готово.")
    return True


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
    if await state.get_state() == AssignDraft.await_window.state:
        return await _window_reply(message, state, text)
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
async def assign_pick(callback: CallbackQuery, state: FSMContext | None = None) -> None:
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

    await _show_card(callback.message, res, state)


@router.callback_query(F.data.startswith("evwin:"))
async def assign_window(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Время: имя»: спрашиваем «19:30–21:00» для выбранного человека.

    Человек выбирается кнопкой, а не разбором имени из текста: в карточке уже
    есть состав, и повторный разбор разошёлся бы с ним (V-134).
    """
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("Кнопка устарела, начните назначение заново", show_alert=True)
        return
    data = await state.get_data()
    card = (data.get(PREVIEW_KEY) or {}).get("card") or {}
    person = next((item for item in card.get("participants") or []
                   if int(item["id"]) == int(parts[2])), None)
    if person is None:
        await callback.answer("Кнопка устарела, начните назначение заново", show_alert=True)
        return
    await callback.answer()
    await state.update_data(**{WINDOW_PERSON_KEY: {"id": person["id"],
                                                   "name": person.get("name") or ""}})
    await state.set_state(AssignDraft.await_window)
    await callback.message.answer(
        f"Время участия: {person.get('name')}. Напишите «19:30–21:00» или «весь» "
        "— тогда человек задействован всё событие.", reply_markup=draft_cancel_kb())


@router.callback_query(F.data.startswith("evok:"))
async def assign_confirm(callback: CallbackQuery, state: FSMContext | None = None) -> None:
    proposal_id = int(callback.data.split(":", 1)[1])
    await callback.answer("Записываю")
    payload: dict = {"proposal_id": proposal_id}
    data = await state.get_data() if state is not None else {}
    stored = data.get(PREVIEW_KEY) or {}
    windows = _windows_payload(data)
    # Окна применяются только к той заявке, для которой их задали: старая кнопка
    # «Назначить» из прошлого назначения не должна утащить чужие окна.
    if windows and int(stored.get("proposal_id") or 0) == proposal_id:
        payload["windows"] = windows
    res = await core.api("/tools/events/assign", payload,
                    callback.message.chat.id)
    if state is not None:
        await state.clear()
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
    windows = [person for person in card.get("participants") or []
               if person.get("time_from") or person.get("time_to")]
    if windows:
        lines.append("Время участия: " + ", ".join(
            f"{person['name']} {person.get('time_from') or '—'}–{person.get('time_to') or '—'}"
            for person in windows))
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

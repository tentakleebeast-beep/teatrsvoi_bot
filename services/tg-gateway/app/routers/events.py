"""Карточка события в чате: отмена и перенос из неё (С2, С3; FR-D18, FR-D19).

Дом действия — карточка: человек видит, что именно отменяет или переносит,
и подтверждает до записи. Карточка приходит после назначения события и
переоткрывается по кнопке «Назад»/из старых сообщений: данные берутся из
`GET /tools/events/{id}`, а не из текста сообщения — событие живёт в базе,
даже если календарь был недоступен.

Путь записи один: `POST /tools/events/cancel` и `POST /tools/events/move`.
Второго обработчика чат не заводит.

Обоснование: FR-D18, FR-D19, карта t_dfb5f169 (Ш-06), docs/reports/124 §3.
"""

from __future__ import annotations

import re
from datetime import date as _date

import core
import errors
import keyboards
import parsers
import texts
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from states import EventMove

router = Router()

#: Рабочий день, в котором разрешён перенос (то же правило, что у API).
WORK_START = 8
WORK_END = 23


def _event_id(callback: CallbackQuery, prefix: str) -> int | None:
    """id события из callback_data: битая или старая кнопка — None, а не ValueError."""
    raw = (callback.data or "")[len(prefix):]
    return int(raw) if raw.isdigit() else None


async def _load(chat_id: int, event_id: int) -> dict | None:
    res = await core.api(f"/tools/events/{event_id}", None, chat_id, method="GET")
    return res.get("event") if res.get("ok") else None


def card_text(event: dict) -> str:
    """Карточка события: тип, название, день, часы, помещение, состав."""
    lines = [f"{event['kind_title']}: {event['title']}",
             _when_text(event["date"], event["from_hour"], event["to_hour"])]
    if event.get("venue_name") or event.get("venue"):
        lines.append(f"Помещение: {event.get('venue_name') or event['venue']}")
    names = [person.get("name") or "" for person in event.get("participants") or []]
    names = [name for name in names if name]
    if names:
        lines.append("Участники: " + ", ".join(names))
    if event.get("status") == "cancelled":
        lines.append("Событие отменено.")
    return "\n".join(lines)


def _when_text(day: str, from_hour: int, to_hour: int) -> str:
    weekday = texts.WEEKDAY_TITLES.get(_date.fromisoformat(day).weekday(), "")
    return (f"{weekday}, {day[8:10]}.{day[5:7]}, "
            f"{from_hour:02d}:00–{to_hour:02d}:00")


def event_markup(event_id: int) -> InlineKeyboardMarkup:
    """Кнопки карточки: отмена и перенос — оба с подтверждением до записи."""
    return keyboards.kb([[("Отменить", f"evcancel:{event_id}"),
                          ("Перенести", f"evmove:{event_id}")]])


async def send_card(message: Message, event: dict) -> None:
    """Показать карточку события с действиями — после назначения и по повторному входу."""
    if event.get("status") == "cancelled":
        await message.answer(card_text(event))
        return
    await message.answer(card_text(event), reply_markup=event_markup(event["event_id"]))


async def _leave_form(state: FSMContext) -> None:
    """Выход из формы переноса: карточка, «Нет» и отмена снимают `EventMove`.

    Иначе после «Отмены» в подтверждении остаётся `EventMove.await_time`
    с прежними `ev_new_*`, и следующий свободный текст уходит в разбор даты
    вместо обычной обработки. Чужие формы не трогаем: карточку можно открыть
    из старого сообщения посреди другой записи, и терять её нельзя.
    """
    current = await state.get_state()
    name = getattr(current, "state", current) or ""
    if name.startswith("EventMove"):
        await state.clear()


@router.callback_query(F.data.startswith("evcard:"))
async def event_card(cb: CallbackQuery, state: FSMContext) -> None:
    """Повторно открыть карточку события: кнопка «Назад», «Нет» и старые сообщения."""
    await _leave_form(state)
    event_id = _event_id(cb, "evcard:")
    if event_id is None:
        await cb.answer("Кнопка устарела, откройте расписание заново", show_alert=True)
        return
    await cb.answer()
    event = await _load(cb.message.chat.id, event_id)
    if event is None:
        await cb.message.answer("Событие не нашлось: возможно, его уже отменили.")
        return
    await send_card(cb.message, event)


@router.callback_query(F.data.startswith("evcancel:"))
async def event_cancel_ask(cb: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение отмены: до записи человек видит, что снимается."""
    parts = (cb.data or "").split(":")
    if len(parts) == 3 and parts[1] == "yes":
        await _cancel(cb, parts[2], state)
        return
    raw = (cb.data or "")[len("evcancel:"):]
    if not raw.isdigit():
        await cb.answer("Кнопка устарела, откройте расписание заново", show_alert=True)
        return
    await _leave_form(state)        # отмена — выход из формы переноса
    event_id = int(raw)
    await cb.answer()
    event = await _load(cb.message.chat.id, event_id)
    if event is None:
        await cb.message.answer("Событие не нашлось: возможно, его уже отменили.")
        return
    if event.get("status") == "cancelled":
        await cb.message.answer(card_text(event))
        return
    await cb.message.answer(
        "Отменить событие?\n\n" + card_text(event) +
        "\n\nСобытие снимется из календаря и расписания, участники получат уведомление.",
        reply_markup=keyboards.kb([
            [("Да, отменить", f"evcancel:yes:{event_id}")],
            [("Нет, оставить", f"evcard:{event_id}")],
        ]))


async def _cancel(cb: CallbackQuery, raw_id: str, state: FSMContext) -> None:
    if not raw_id.isdigit():
        await cb.answer("Кнопка устарела, откройте расписание заново", show_alert=True)
        return
    await _leave_form(state)        # отмена завершена (или не нашлась) — формы больше нет
    event_id = int(raw_id)
    await cb.answer("Отменяю")
    res = await core.api("/tools/events/cancel", {"event_id": event_id},
                         cb.message.chat.id)
    if not res.get("ok"):
        code = errors.error_code(res)
        await cb.message.answer({
            "event_not_found": "Событие не нашлось: возможно, его уже отменили.",
            "not_your_event": "Отменить может организатор события или администратор.",
        }.get(code, f"Не получилось отменить: {errors.error_text(res)}"))
        return

    lines = ["Отменено."]
    if res.get("already_cancelled"):
        lines = ["Событие уже было отменено."]
    if res.get("notified"):
        lines.append(f"Участникам поставлены уведомления: {res['notified']}.")
    if res.get("calendar_error"):
        lines.append("Календарь не ответил — событие снято из расписания, "
                     "в календаре проверить вручную.")
    await cb.message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("evmove:"))
async def event_move_ask(cb: CallbackQuery, state: FSMContext) -> None:
    raw = (cb.data or "")[len("evmove:"):]
    if raw == "yes":
        await _move(cb, state)
        return
    if not raw.isdigit():
        await cb.answer("Кнопка устарела, откройте расписание заново", show_alert=True)
        return
    await cb.answer()
    event = await _load(cb.message.chat.id, int(raw))
    if event is None:
        await cb.message.answer("Событие не нашлось: возможно, его уже отменили.")
        return
    if event.get("status") == "cancelled":
        await cb.message.answer(card_text(event))
        return

    duration = max(1, int(event["to_hour"]) - int(event["from_hour"]))
    await state.update_data(
        ev_id=event["event_id"], ev_title=event["title"], ev_kind=event["kind_title"],
        ev_day=event["date"], ev_from=int(event["from_hour"]), ev_to=int(event["to_hour"]),
        ev_duration=duration,
        ev_people={str(person["id"]): person.get("name") or ""
                   for person in event.get("participants") or []})
    await state.set_state(EventMove.await_time)
    await cb.message.answer(
        f"{event['kind_title']}: {event['title']}\n"
        f"Сейчас: {_when_text(event['date'], int(event['from_hour']), int(event['to_hour']))}\n\n"
        "На когда перенести? Напишите дату и время начала, например: 07.11 19:00. "
        f"Длительность останется прежней — {duration} ч.")


@router.message(EventMove.await_time, F.text)
async def event_move_text(message: Message, state: FSMContext) -> None:
    # Любое состояние обязано иметь выход: кнопки меню работают всегда (инцидент 28.10)
    if message.text.strip() in keyboards.MENU_SURFACE:
        from routers import menu as menu_router  # noqa: PLC0415 — цикл импортов

        await menu_router.menu_action(message, state)
        return

    data = await state.get_data()
    today = await core.today_local(message.chat.id)
    parsed = parse_when(message.text, today)
    if parsed is None:
        await message.answer("Не понял дату и время. Например: 07.11 19:00")
        return
    day, from_hour = parsed
    duration = int(data.get("ev_duration") or 2)
    to_hour = from_hour + duration
    if from_hour < WORK_START or to_hour > WORK_END:
        await message.answer(
            f"Рабочий день {WORK_START:02d}:00–{WORK_END:02d}:00, событие на {duration} ч "
            "туда не помещается. Назовите другое время.")
        return

    await state.update_data(ev_new_day=day.isoformat(), ev_new_from=from_hour,
                            ev_new_to=to_hour)
    old = _when_text(str(data.get("ev_day")), int(data.get("ev_from") or 0),
                     int(data.get("ev_to") or 0))
    new = _when_text(day.isoformat(), from_hour, to_hour)
    await message.answer(
        f"Перенести: {data.get('ev_kind')} «{data.get('ev_title')}»?\n"
        f"Было: {old}\nСтанет: {new}",
        reply_markup=keyboards.kb([
            [("Перенести", "evmove:yes")],
            [("Отмена", f"evcard:{data.get('ev_id')}")],
        ]))


async def _move(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    event_id = data.get("ev_id")
    day = data.get("ev_new_day")
    if not event_id or not day:
        await cb.answer("Кнопка устарела, откройте карточку события заново", show_alert=True)
        return
    await cb.answer("Переношу")
    res = await core.api("/tools/events/move", {
        "event_id": int(event_id), "date": day,
        "from_hour": int(data.get("ev_new_from")),
        "to_hour": int(data.get("ev_new_to")),
    }, cb.message.chat.id)

    code = errors.error_code(res)
    if not res.get("ok"):
        if code in ("slot_taken", "outside_working_hours", "bad_interval"):
            # время не подошло — спрашиваем другое, состояние не теряем
            money = {"slot_taken": "Это время занято в календаре.",
                     "outside_working_hours": "Это время вне рабочего дня 08:00–23:00.",
                     "bad_interval": "Конец раньше начала."}.get(code, "Время не подошло.")
            await cb.message.answer(f"{money} Назовите другое: дата и время начала.")
            return
        await cb.message.answer({
            "event_not_found": "Событие не нашлось: возможно, его уже отменили.",
            "event_cancelled": "Событие отменено — переносить нечего.",
            "not_your_event": "Перенести может организатор события или администратор.",
        }.get(code, f"Не получилось перенести: {errors.error_text(res)}"))
        return

    await state.clear()
    old = _when_text(str(data.get("ev_day")), int(data.get("ev_from") or 0),
                     int(data.get("ev_to") or 0))
    new = _when_text(day, int(data.get("ev_new_from")), int(data.get("ev_new_to")))
    lines = [f"Перенесено: {data.get('ev_kind')} «{data.get('ev_title')}»",
             f"Было: {old}", f"Стало: {new}"]
    people = data.get("ev_people") or {}
    busy = [people.get(str(pid), "") for pid in res.get("busy_people") or []]
    busy = [name for name in busy if name]
    if busy:
        lines.append("По отметкам заняты: " + ", ".join(busy) + ".")
    if res.get("calendar_error"):
        lines.append("Календарь не ответил — время изменено в расписании, "
                     "в календаре проверить вручную.")
    await cb.message.answer("\n".join(lines))


_MONTHS = parsers.MONTHS


def parse_when(text: str, today: _date) -> tuple[_date, int] | None:
    """«07.11 19:00», «7 ноября в 19», «завтра 18:30» → (день, час начала).

    Дата обязательна: перенос «на сегодня же» по умолчанию — это угадывание,
    которого в необратимом действии быть не должно. API принимает целый час
    начала, поэтому «19:30» читается как 20:00 — человек видит это в карточке
    подтверждения и может назвать другое время.
    """
    raw = " ".join((text or "").lower().replace("ё", "е").split())
    day: _date | None = None

    match = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", raw)
    if match:
        day_number, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            day = _date(year, month, day_number)
        except ValueError:
            return None
        raw = raw.replace(match.group(0), " ")
    else:
        for stem, month in _MONTHS.items():
            found = re.search(rf"\b(\d{{1,2}})\s+{stem}", raw)
            if found:
                try:
                    day = _date(today.year, month, int(found.group(1)))
                except ValueError:
                    return None
                raw = raw.replace(found.group(0), " ")
                break
        if day is None:
            for word, offset in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
                if word in raw:
                    day = _date.fromordinal(today.toordinal() + offset)
                    raw = raw.replace(word, " ")
                    break
    if day is None:
        return None

    clock = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?", raw)
    if not clock:
        return None
    hour = int(clock.group(1))
    if not 0 <= hour <= 23:
        return None
    if clock.group(2) and int(clock.group(2)) >= 30:
        hour += 1                    # 19:30 → ближайший целый час начала, а не 19:00
        if hour > 23:
            return None
    return day, hour

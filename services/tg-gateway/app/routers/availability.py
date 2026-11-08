"""Роутер занятости: отметки дней, поиск окон, расписание."""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import timedelta as _td
from html import escape

import core
import errors
import keyboards
import parsers
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from availability_hours import outside_work_day
from routing import MAX_FREE_Q
from states import AvailForm
from webapp_links import keyboard as webapp_keyboard

import config
import routers.menu as menu_router

router = Router()

SLOTS_PAGE = "/webapp/slots"


def slots_entry():
    """Кнопка «в дом»: экран поиска окон — единственное место, где виден весь
    результат и можно перебрать критерии (правило «дом + вход», docs/architecture/40 §5).
    Пока адрес Mini App не задан, кнопки нет — вместо неё в тексте остаётся ответ.

    Адрес читается из `config` в момент вызова: тесты подменяют его без импорта,
    и «кнопки нет» проверяется честно, а не по копии значения в модуле.
    """
    return webapp_keyboard("Открыть поиск окон", SLOTS_PAGE, config.WEBAPP_BASE_URL)


async def answer_slots(message: Message, text: str | None = None,
                       min_hours: int = 2) -> bool:
    """Поиск общих окон: считает tool-api, бот только показывает результат.

    Дом действия — экран `/webapp/slots` (С6, docs/architecture/40 §5), чат —
    вход: распознаёт намерение, показывает результат и даёт кнопку в дом.
    """
    payload = {"min_hours": min_hours, "limit": 3, "text": text}
    kind = parsers.detect_event_kind(text or "")
    if kind:
        payload["policy"] = kind
        if kind in ("show", "rental"):
            payload["min_hours"] = max(min_hours, 2)
    elif not re.search(MAX_FREE_Q, (text or "").lower()):
        # Тип не назван — берём репетицию как самый частый случай
        # (docs/architecture/42 §6). Иначе «найди окна на неделю» считалось бы
        # без дороги до театра и без правил типа: другой ответ на тот же вопрос.
        payload["policy"] = "rehearsal"

    res = await core.api("/tools/schedule/slots", payload, message.chat.id)
    if not res.get("ok"):
        await message.answer("Нет доступа к поиску окон или данных о занятости нет.",
                             reply_markup=slots_entry())
        return True

    meta = res.get("meta") or {}
    resolved = res.get("resolved") or {}

    if res.get("mode") in ("policy", "venue"):
        # mode="venue" — область субаренды: окно считается по залу (docs/architecture/40 §4)
        title = res.get("policy_title") or "событие"
        travels = res.get("travel_minutes") or {}
        line = f"Окна для «{title}» (минимум {min_hours} ч"
        if travels:
            shortest, longest = min(travels.values()), max(travels.values())
            line += (f", дорога {shortest} мин" if shortest == longest
                     else f", дорога {shortest}–{longest} мин")
        line += "):"
        lines = [line, ""]
        if res.get("mode") == "venue" and res.get("reason") == "venue_unknown":
            lines.append("Занятость зала неизвестна: календарь не отдал ни одного "
                         "события за эти дни. Окна не предлагаю — свободное время "
                         "зала нужно сверить вручную.")
            await message.answer("\n".join(lines)[:3800], reply_markup=slots_entry())
            return True
        if res.get("text"):
            lines += [f"{i}. {t}" for i, t in enumerate(res["text"], 1)]
        else:
            lines.append("Подходящих окон нет — попробуйте другой день или короче.")
        venue = res.get("venue") or {}
        if res.get("policy") in ("rental", "rental_staff") and venue.get("applied"):
            lines += ["", f"Зал проверен по календарю: занятых интервалов "
                          f"{venue.get('busy', 0)}."]
        elif res.get("policy") in ("rental", "rental_staff"):
            lines += ["", "Календарь зала не подключён — свободное время "
                          "нужно сверить вручную."]
        await message.answer("\n".join(lines)[:3800], reply_markup=slots_entry())
        return True

    if res.get("mode") == "max_free":
        lines = [f"Где больше всего свободных (минимум {min_hours} ч):", ""]
        lines += [f"{i}. {t}" for i, t in enumerate(res.get("text") or [], 1)]
        if resolved.get("unresolved"):
            lines += ["", "Не понял: " + ", ".join(resolved["unresolved"])]
        await message.answer("\n".join(lines)[:3800], reply_markup=slots_entry())
        return True

    if resolved.get("unresolved") and resolved.get("confidence", 1) < 0.5:
        names = ", ".join(resolved["unresolved"])
        await message.answer(
            f"Не понял, кого искать: {names}.\n"
            f"Напишите имена, роли или «для всех» — тогда посчитаю окна.")
        return True

    if not res.get("windows"):
        await message.answer(
            f"Общих окон на {meta.get('days', 7)} дней (минимум {min_hours} ч) нет.\n"
            f"Участников в расчёте: {meta.get('participants', 0)}.\n\n"
            "Попросите отметить занятость — тогда появится, из чего выбирать.",
            reply_markup=keyboards._buttons_with_week([[(texts.TITLE_AVAILABILITY, "form:busy")]]))
        return True

    subject = resolved.get("hint") if resolved.get("mode") == "subset" else "вся труппа"
    lines = [f"Окна на {meta.get('days', 7)} дней, минимум {min_hours} ч.",
             f"Кто участвует: {subject}", ""]
    for index, text in enumerate(res.get("text") or [], start=1):
        lines.append(f"{index}. {text}")

    unknown = res.get("unknown") or []
    total = int(meta.get("participants") or 0)
    if unknown:
        if total and len(unknown) * 2 >= total:
            lines += ["", f"Данных почти нет: отмечено {total - len(unknown)} из {total} — "
                          f"окно условное, его надо подтвердить у людей."]
        lines += ["", "Без отметок: " + ", ".join(item["name"] for item in unknown)[:900]]

    lines += ["", "Часов в расчёте: " + str(meta.get("cells", 0))]
    await message.answer("\n".join(lines)[:3800],
                         reply_markup=keyboards.kb([[("Искать на 3 часа", "slots:3"),
                                           (texts.TITLE_SCHEDULE, "panel:root")]]))
    return True


@router.callback_query(F.data.startswith("slots:"))
async def slots_more(cb: CallbackQuery) -> None:
    raw = (cb.data or "").split(":", 1)[1] if ":" in (cb.data or "") else ""
    if not raw.isdigit() or not 1 <= int(raw) <= 12:
        # битая или устаревшая кнопка: раньше здесь был ValueError
        await cb.answer("Кнопка устарела, откройте поиск заново", show_alert=True)
        return
    await cb.answer()
    await answer_slots(cb.message, min_hours=int(raw))


async def answer_schedule(message: Message) -> bool:
    """«Что на неделе» — из календаря и занятости, без модели."""
    caps = await core.caps_of(message.chat.id)
    if "schedule.read.all" not in caps and "*" not in caps:
        await message.answer("Сводное расписание доступно организатору и администратору.")
        return True

    ev = await core.api("/tools/calendar/events?days=7", None, message.chat.id, method="GET")
    av = await core.api("/tools/availability/summary?days=7", None, message.chat.id, method="GET")
    if ev.get("ok") and ev.get("count"):
        lines = texts.schedule_lines(ev["data"])
    elif ev.get("ok"):
        lines = [texts.SCHEDULE_HEADER, "", "в календаре событий нет"]
    else:
        lines = [texts.SCHEDULE_HEADER]

    if av.get("ok") and av.get("data"):
        lines.append("")
        lines.extend(texts.availability_lines(av["data"]))
        # покрытие (FR-C7): свод без этой строки выглядит полным, даже когда
        # отметилась половина группы — человек должен видеть, чему верить
        coverage_line = (av.get("coverage") or {}).get("line")
        if coverage_line:
            lines.append("")
            lines.append(escape(coverage_line))
    await message.answer(texts.html_join(lines), parse_mode="HTML")
    return True


def _time_kb():
    """Время дня: край дня, два привычных интервала и свой (C-5, обращение №34)."""
    return keyboards.kb([[("Весь день", "av:t:0-24")],
                         [("После 18", "av:t:18-24")],
                         [("До 15", "av:t:0-15")],
                         [("Своё время", "av:t:custom")]])


def _note_of(text: str) -> str:
    """Комментарий из фразы: «занят до 18 (в Самаре)» → «в Самаре» (FR-C9)."""
    match = re.search(r"\(([^)]*)\)", text or "")
    return " ".join(match.group(1).split())[:texts.NOTE_LIMIT] if match else ""


def _status_of(kind: str | None) -> str:
    """Вид отметки из разбора → состояние карточки дня (`WeekDayIn`)."""
    return "free" if kind in ("free", "preferred") else "busy"


def _raw_text(status: str, start: int, end: int, note: str = "") -> str:
    """Текст отметки для листа — канон карточки дня, а не своя формулировка (FR-C9)."""
    label = f"{texts.STATUS_WORDS.get(status, status)} с {start:02d}:00 до {end:02d}:00"
    return f"{label} ({note})" if note else label


def _bounds(start: int, end: int) -> tuple[int | None, int | None]:
    """Границы для карточки дня: край дня — это пустая граница, а не 0 и не 24.

    Карточка дня (`WeekDayIn`) живёт в рабочем дне 08:00–24:00: `from_time=None`
    значит «с начала дня», `to_time=None` — «до конца». Явный час до восьми
    (например «с 5 до 9») карточкой не выражается — его пишет отметка по часам.
    """
    if 0 < start < 8:
        return start, end
    low = None if start <= 8 else start
    high = None if end >= 24 else end
    return low, high


def _before_work_day(start: int) -> bool:
    """Явный час до начала рабочего дня — единственный случай не карточки."""
    return 0 < start < 8


async def _ask_status(message: Message) -> None:
    await message.answer(texts.QUESTION_STATUS, reply_markup=keyboards.status_kb())


async def _ask_note(message: Message, state: FSMContext) -> None:
    """Комментарий к дню необязателен, но спрашиваем: он едет в лист и в сводку."""
    await state.set_state(AvailForm.note)
    await message.answer(texts.QUESTION_NOTE, reply_markup=keyboards.skip_note_kb())


async def _confirm(message: Message, state: FSMContext, *, edit: bool = False) -> None:
    """Карточка подтверждения: состояние, границы и комментарий — до записи."""
    data = await state.get_data()
    start, end = data.get("av_start"), data.get("av_end")
    if start is not None and end is not None:
        start, end = _bounds(int(start), int(end))
    text = texts.day_card_text(data.get("av_day") or "", data.get("av_kind") or "busy",
                               start, end, data.get("av_note") or "")
    markup = keyboards.kb(BUSY_CARD_ROWS)
    await state.set_state(AvailForm.confirm)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("av:day:"))
async def av_day(cb: CallbackQuery, state: FSMContext) -> None:
    val = (cb.data or "").split(":")[2]
    if val == "other":
        await cb.message.edit_text("Напишите дату: 28.10")
        await cb.answer()
        return
    if not val.isdigit():
        await cb.answer("Кнопка устарела, откройте форму заново", show_alert=True)
        return
    day = ((await core.today_local(cb.message.chat.id)) + _td(days=int(val))).isoformat()
    await state.update_data(av_day=day, av_note="")
    await state.set_state(AvailForm.status)
    await cb.message.edit_text(texts.QUESTION_STATUS, reply_markup=keyboards.status_kb())
    await cb.answer()


@router.message(AvailForm.day, F.text)
async def av_day_text(message: Message, state: FSMContext) -> None:
    # Выход из формы: кнопки меню работают в любом состоянии (инцидент 28.10)
    if message.text.strip() in keyboards.MENU_SURFACE:
        await menu_router.menu_action(message, state)
        return
    m = re.match(r"(\d{1,2})[.\s/](\d{1,2})", message.text.strip())
    if not m:
        await state.clear()
        await message.answer(
            "Не понял дату. Попробуйте ещё раз или обратитесь к разработчику — "
            "форма закрыта, начать заново можно кнопкой «Занятость».")
        return
    try:
        base = await core.today_local(message.chat.id)
        day = _date(base.year, int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        await state.clear()
        await message.answer(
            "Дата не подходит. Например: 28.10. Форма закрыта, начать заново — "
            "кнопкой «Занятость».")
        return
    await state.update_data(av_day=day, av_note="")
    await _ask_status(message)


@router.callback_query(F.data.startswith("av:s:"))
async def av_status(cb: CallbackQuery, state: FSMContext) -> None:
    """Состояние дня: занят / свободен / не уверен (FR-D6, `WeekDayIn.status`)."""
    status = (cb.data or "").split(":")[2]
    if status not in texts.STATUS_WORDS:
        await cb.answer("Кнопка устарела, откройте форму заново", show_alert=True)
        return
    await state.update_data(av_kind=status)
    await cb.answer()
    if status == "uncertain":
        # «Не уверен» — состояние дня без границ: модель не разрешает их вместе
        await state.update_data(av_start=None, av_end=None)
        await state.set_state(AvailForm.note)
        await cb.message.edit_text(texts.QUESTION_NOTE, reply_markup=keyboards.skip_note_kb())
        return
    await state.set_state(AvailForm.time)
    await cb.message.edit_text("Время?", reply_markup=_time_kb())


@router.callback_query(F.data.startswith("av:t:"))
async def av_time(cb: CallbackQuery, state: FSMContext) -> None:
    val = (cb.data or "").split(":")[2]
    if val == "custom":
        await cb.message.edit_text("Напишите интервал: с 10 до 14")
        await cb.answer()
        return
    try:
        start, end = (int(x) for x in val.split("-"))
    except ValueError:
        await cb.answer("Кнопка устарела, откройте форму заново", show_alert=True)
        return
    await state.update_data(av_start=start, av_end=end)
    await cb.answer()
    await state.set_state(AvailForm.note)
    await cb.message.edit_text(texts.QUESTION_NOTE, reply_markup=keyboards.skip_note_kb())


@router.message(AvailForm.time, F.text)
async def av_time_text(message: Message, state: FSMContext) -> None:
    # Любое состояние обязано иметь выход: кнопки меню работают всегда (инцидент 28.10)
    if message.text.strip() in keyboards.MENU_SURFACE:
        await menu_router.menu_action(message, state)
        return

    res = await core.api("/tools/availability/parse-interval", {"text": message.text},
                    message.chat.id)
    if not res.get("ok"):
        # Не держим человека в строгом ожидании: говорим прямо и выходим из формы
        await state.clear()
        await message.answer(
            "Не понял время. Попробуйте ещё раз или обратитесь к разработчику.\n"
            "Занятость можно внести заново кнопкой «Занятость» — день, потом интервал, "
            "например «с 10 до 14».")
        return

    await state.update_data(av_start=res["start"], av_end=res["end"],
                            av_outside=bool(res.get("outside_work_day")),
                            av_day=(await state.get_data()).get("av_day") or res.get("day"))
    await _ask_note(message, state)


@router.message(AvailForm.note, F.text)
async def av_note_text(message: Message, state: FSMContext) -> None:
    """Комментарий к дню: короткий, без переводов строк — он едет в лист одной строкой."""
    if message.text.strip() in keyboards.MENU_SURFACE:
        await menu_router.menu_action(message, state)
        return
    await state.update_data(av_note=" ".join(message.text.split())[:texts.NOTE_LIMIT])
    await _confirm(message, state)


@router.callback_query(F.data == "av:n:skip")
async def av_note_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(av_note="")
    await cb.answer()
    await _confirm(cb.message, state, edit=True)


@router.callback_query(F.data == "av:ok")
async def av_save(cb: CallbackQuery, state: FSMContext) -> None:
    """Запись дня карточкой: тот же путь, что и до формы, — второго не заводим.

    Два входа в одну карточку: свободный текст (`/busy`, AUD-4) пишет отметку по
    часам через `POST /tools/availability/commit`; форма дня пишет карточку дня
    через `POST /tools/availability/week` — тем же путём, что экран
    (`/webapp/availability/week`, общий `_save_week`). «Не уверен» выражается
    только карточкой: `commit` пишет часы и ставит `is_free = false`, а модель
    держит «не уверен» с `is_free = true` (`availability_summary`).
    """
    data = await state.get_data()
    card = data.get("av") or {}                 # карточка свободного текста или `/busy`
    day = data.get("av_day") or card.get("day")
    status = data.get("av_kind") or _status_of(card.get("kind"))
    note = (data.get("av_note") or "").strip() or _note_of(data.get("raw") or "")
    start = data.get("av_start", card.get("start"))
    end = data.get("av_end", card.get("end"))
    # C-5: 00–07 не запрещены, но человек подтверждает их отдельно — флаг уходит в API.
    # 0 — полночь, а не «не задано»: `start or 8` превращал «Весь день» в 08–24
    # и API отвечал 400 outside_work_day (обращение №34).
    if not day:
        await cb.answer("Не хватает даты", show_alert=True)
        return
    if status == "uncertain":
        res = await core.api("/tools/availability/week", {
            "start": day,
            "days": [{"day": day, "status": "uncertain", "note": note or None}],
        }, cb.message.chat.id)
    elif card or _before_work_day(int(start or 0)):
        # Отметка по часам: свободный текст (AUD-4) и часы до 08:00 (C-5), которые
        # карточкой дня не выражаются. Путь записи прежний — `commit`.
        outside = bool(data.get("av_outside") or card.get("outside_work_day")
                       or outside_work_day(int(start), int(end)))
        res = await core.api("/tools/availability/commit", {
            "chat_id": cb.message.chat.id, "day": day, "start": int(start),
            "end": int(end), "kind": card.get("kind") or status,
            "raw_text": _raw_text(status, int(start), int(end), note),
            "allow_outside_hours": outside,
        }, cb.message.chat.id)
    else:
        if start is None or end is None:
            await cb.answer("Не хватает времени", show_alert=True)
            return
        start, end = _bounds(int(start), int(end))
        res = await core.api("/tools/availability/week", {
            "start": day,
            "days": [{"day": day, "status": status,
                      "from_time": None if start is None else f"{start:02d}:00",
                      "to_time": None if end is None else f"{end:02d}:00",
                      "note": note or None}],
        }, cb.message.chat.id)
    await state.clear()
    if res.get("ok"):
        await cb.message.edit_text(texts.PREFIX_WRITTEN + texts.day_card_text(
            day, status, start, end, note, question=False))
        await cb.answer(texts.BTN_DONE)
    else:
        detail = errors.error_text(res)
        hint = ("Часы вне рабочего дня 08–23 — их нужно подтвердить отдельно."
                if "outside_work_day" in detail else texts.SAVE_FAILED)
        await cb.answer(hint, show_alert=True)


@router.callback_query(F.data == "av:no")
async def av_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(texts.CANCELLED)
    await cb.answer()


@router.callback_query(F.data == "av:fix")
async def av_fix(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AvailForm.day)
    await cb.message.edit_text("Напишите дату и время: «в среду после 18» или «28.10 с 10 до 14»")
    await cb.answer()


#: Карточка занятости: подтверждение, правка формой или отказ. Одна раскладка на
#: оба входа — команду `/busy` и свободный текст (AUD-4), чтобы кнопки не разошлись.
BUSY_CARD_ROWS: list[list[tuple[str, str]]] = [
    [("Верно", "av:ok"), ("Исправить", "form:busy")],
    [(texts.BTN_CANCEL, "av:no")],
]


async def busy_card(message: Message, text: str, state: FSMContext) -> bool:
    """Занятость из свободного текста: разбор → карточка → подтверждение.

    Тот же путь, что у `/busy`: `POST /tools/availability/parse` разбирает фразу,
    запись идёт только по кнопке «Верно» через `av_save` → `POST .../commit`.
    Нового разбора и новой записи здесь нет.

    Возвращает True, если человеку уже ответили; False — фразу разобрать нельзя
    (тогда вызывающий сам решает, что показать).

    Часы вне рабочего дня 08:00–23:00 карточкой не подтверждаются: занятость
    вне дня студии не пишем (docs/architecture/11, раздел «Рабочий день студии»).
    """
    caps = await core.caps_of(message.chat.id)
    if not core.has(caps, "schedule.availability.self"):
        await message.answer("Занятость отмечает участник студии.")
        return True

    res = await core.api("/tools/availability/parse", {"text": text}, message.chat.id)
    if not res.get("ok"):
        if errors.error_code(res) == "need_date":
            await message.answer(
                "Не понял, на какой день. День нужен в тексте — например "
                "«занят завтра с 10 до 14».")
            return True
        return False

    if res.get("outside_work_day"):
        # границы 08–23 — правило всех каналов; отказ текстом, а не запись
        await message.answer(
            "Рабочий день студии 08:00–23:00 — часы вне него не записываю.\n"
            "Напишите интервал внутри дня, например «занят завтра с 10 до 14».")
        return True

    note = _note_of(text)
    label = res["label"] + (f" ({note})" if note else "")
    await state.clear()
    await state.update_data(av=res, raw=text, av_day=res.get("day"),
                            av_start=res.get("start"), av_end=res.get("end"),
                            av_kind=_status_of(res.get("kind")),
                            av_outside=bool(res.get("outside_work_day")), av_note=note)
    await message.answer(
        f"Записываю занятость:\n{label}\n\nВерно?",
        reply_markup=keyboards.kb(BUSY_CARD_ROWS))
    return True


@router.message(Command("busy"))
async def cmd_busy(message: Message, state: FSMContext) -> None:
    """/busy занят 28.10 с 10 до 14 — занятость командой (Ш-52)."""
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    caps = await core.caps_of(message.chat.id)
    if not core.has(caps, "schedule.availability.self"):
        await message.answer("Занятость отмечает участник студии.")
        return
    await state.clear()
    if not arg:
        await state.set_state(AvailForm.day)
        await message.answer(
            texts.QUESTION_WHEN, reply_markup=keyboards.kb([[(texts.BTN_TODAY, "av:day:0"), (texts.BTN_TOMORROW, "av:day:1")],
                                       [(texts.BTN_OTHER_DATE, "av:day:other")]]))
        return
    if not await busy_card(message, arg, state):
        await message.answer("Не понял дату и время. Пример: /busy занят 28.10 с 10 до 14")


# ------------------------------------------ «обычная неделя» из чата (З5, FR-C10)
# Серверные пути те же, что у экрана: `POST /tools/availability/template/from-week` и
# `…/template/apply` (в Mini App — `/webapp/availability/template/*`, тот же обработчик).
WEEK_COMMANDS = ("/week", "/неделя", "/обычная")


def _week_start(day: _date) -> str:
    """Понедельник недели дня. Сервер принимает любой день и приводит сам."""
    return (day - _td(days=day.weekday())).isoformat()


async def _week_view(chat_id: int, notice: str = "") -> tuple[str, object]:
    """Текст и кнопки «Обычной недели» по данным сервера (одно чтение на оба входа)."""
    res = await core.api("/tools/availability/template", None, chat_id, method="GET")
    if not res.get("ok"):
        return texts.WEEK_UNAVAILABLE, None
    text = texts.week_text(res)
    if notice:
        text = f"{notice}\n\n{text}"
    return text[:3800], keyboards.week_kb()


@router.message(lambda message: parsers.command_name(message.text) in WEEK_COMMANDS)
async def cmd_week(message: Message, state: FSMContext) -> None:
    """`/week` — запомнить текущую неделю и заполнить по ней (FR-C10, карта AUD-5)."""
    chat_id = message.chat.id
    if not core.has(await core.caps_of(chat_id), "schedule.availability.self"):
        await message.answer(texts.WEEK_DENIED)
        return
    await state.clear()
    text, markup = await _week_view(chat_id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("week:"))
async def week_action(cb: CallbackQuery, state: FSMContext) -> None:
    """Кнопки «Обычной недели»: обе зовут существующие серверные пути."""
    chat_id = cb.message.chat.id
    action = (cb.data or "").split(":", 1)[1]
    if not core.has(await core.caps_of(chat_id), "schedule.availability.self"):
        await cb.answer(texts.WEEK_DENIED, show_alert=True)
        return
    await state.clear()
    start = _week_start(await core.today_local(chat_id))
    if action == "remember":
        res = await core.api("/tools/availability/template/from-week",
                             {"start": start}, chat_id)
    elif action == "apply":
        # overwrite=False — отмеченные дни не затираются (FR-C10, `_template_apply`)
        res = await core.api("/tools/availability/template/apply",
                             {"start": start, "apply": True, "overwrite": False}, chat_id)
    else:
        await cb.answer("Кнопка устарела, откройте /week заново", show_alert=True)
        return
    if not res.get("ok"):
        code = errors.error_code(res)
        await cb.answer({
            "nothing_to_save": texts.WEEK_NOTHING_TO_SAVE,
            "no_template": texts.WEEK_NO_TEMPLATE,
        }.get(code, texts.WEEK_UNAVAILABLE), show_alert=True)
        return
    if action == "remember":
        notice = texts.WEEK_SAVED.format(filled=res.get("filled") or 0)
    else:
        notice = texts.WEEK_APPLIED.format(applied=len(res.get("days") or []),
                                          skipped=len(res.get("skipped") or []))
    text, markup = await _week_view(chat_id, notice)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer(notice[:180])

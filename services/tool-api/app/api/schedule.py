"""Расписание и занятость: слоты, сетка недели, шаблоны, лист."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field, field_validator

from .. import availability_store, core, month_grid, sheet_load
from ..availability_card import (
    DayCard,
    DayCardError,
    from_hours,
    parse,
    parse_clock,
    to_hours,
    to_minutes_text,
    to_text,
)
from ..availability_coverage import coverage, week_title
from ..availability_template import TemplateDay
from ..availability_template import fill as template_fill
from ..availability_template import from_week as template_from_week
from ..calendar_blocks import apply_blocks, blocked_hours
from ..errors import ApiError
from ..external import GOOGLE_API_ERRORS
from ..rules import parse_availability, parse_interval
from ..schedule_sheet import (
    cell_a1,
    column_letter,
    find_names_row,
    find_service_column,
    merge_snapshot,
    plan_week,
    snapshot_mismatches,
)
from ..slots import describe, find_windows, rank_hours
from ..subject import Person as SubjectPerson
from ..subject import Project as SubjectProject
from ..subject import parse_subject
from ..venue import busy_for_studio, find_venue_windows
from ..venue import intersect as intersect_venue
from ..window_policy import POLICIES, find_policy_windows, policy_for

router = APIRouter()

@router.get("/webapp/grid")
def webapp_grid(month: str | None = None, who: dict = Depends(core.webapp_actor)) -> dict:
    """
    Сетка занятости на месяц из базы: люди, дни, состояния. Только чтение.

    Читает всю труппу, поэтому нужен `schedule.read.all` (FR-H9, docs/development/92 §7):
    право проверяется здесь, на сервере, а оболочка только не показывает сегмент
    (docs/architecture/25 §9.2, карта t_52bf3106). Отказ — 403, как у остальных
    обработчиков Mini App.

    Записи здесь нет и не будет: месяц — только для глаз (П-3), правка дня живёт
    в листе дня экрана «Время» (З1). Раньше `POST /webapp/grid` умел ставить
    отметку — второй путь записи убран вместе с маршрутом (Ш-04).

    Состояние дня складывается из часов: если хоть один час занят — день занят.
    Пустая клетка означает «не отмечено», это не «свободен» (docs/development/22 §7).
    """
    core._webapp_require(who, "schedule.read.all")
    month = month or datetime.now(core.TZ).strftime("%Y-%m")
    try:
        days = core._month_days(month)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_month") from None

    with core.pool.connection() as conn:
        people = conn.execute(
            "select id, display_name from participant where status <> 'archived' "
            "order by display_name").fetchall()
        marks = availability_store.day_states(conn, days[0], days[-1])

    return {"ok": True, "month": month,
            "days": [day.isoformat() for day in days],
            "weekdays": [day.weekday() for day in days],
            "people": [{"id": p["id"], "name": p["display_name"],
                        "self": p["id"] == who["id"]} for p in people],
            "marks": {str(pid): values for pid, values in marks.items()},
            "states": list(core.GRID_STATES),
            "hours": [core.GRID_HOURS.start, core.GRID_HOURS.stop - 1]}


def _schedule_values() -> tuple[list[list] | None, str]:
    """Значения листа занятости целиком: (строки, причина отказа).

    Лист — источник истины для занятости (docs/development/22 §1), но экран не
    должен падать, если Google недоступен: тогда показываем базу и говорим об
    этом прямо. Поэтому отказ — не исключение, а причина в ответе.
    """
    if not core.SCHEDULE_SPREADSHEET_ID:
        return None, "no_spreadsheet"
    try:
        service = core._sheets()
        _, title = _schedule_sheet_meta(service)
        values = service.spreadsheets().values().get(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
            range=f"'{title}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as e:
        core.log.warning("лист занятости недоступен для сетки месяца: %s", e)
        return None, "sheet_unavailable"
    return values, ""


@router.get("/webapp/month-grid")
def webapp_month_grid(month: str | None = None,
                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Сетка месяца «люди × дни» из Google-таблицы: только чтение (FR-H9, З9).

    Источник — тот же лист «Расписание студийцы», что у импорта и записи недели:
    люди в колонках листа, дни в строках. Экран разворачивает это в привычное
    «люди в строках, дни в колонках» (П-2, П-3).

    База нужна как вторая сторона: где таблица и база расходятся, ответ несёт
    `diff` и текст расхождения, а экран показывает его списком. Ничего не
    правим — перенос правок листа в базу остался в `POST /tools/availability/reconcile`.

    Право одно с остальной сводкой — `schedule.read.all`: месяц читает всю труппу.
    """
    core._webapp_require(who, "schedule.read.all")
    month = month or datetime.now(core.TZ).strftime("%Y-%m")
    try:
        days = core._month_days(month)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_month") from None

    with core.pool.connection() as conn:
        people = conn.execute(
            "select id, display_name from participant where status <> 'archived' "
            "order by display_name").fetchall()
        marks = availability_store.day_states(conn, days[0], days[-1])

    values, reason = _schedule_values()
    return month_grid.build(
        month=month, days=days,
        people=[{"id": p["id"], "name": p["display_name"], "self": p["id"] == who["id"]}
                for p in people],
        marks=marks, sheet_values=values, sheet_reason=reason)




def _check_hours(from_hour: int, to_hour: int, allow_outside: bool) -> None:
    """Рабочий день 08–23 — норма; остальное допустимо, но с отдельным подтверждением.

    Решение заказчика 28.10.2026 (C-5): отметки 00–07 не переписываем молча,
    а просим подтвердить — и в чате, и в Mini App. Проверка общая для всех
    входов записи (З1): если правило разъедется, «ночная» отметка снова начнёт
    проходить одним путём и падать другим.
    """
    if not 0 <= from_hour < to_hour <= 24:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_hours: допустимо 0–24")
    if not core.GRID_HOURS.start <= from_hour < to_hour <= core.GRID_HOURS.stop and not allow_outside:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"outside_work_day: рабочий день {core.GRID_HOURS.start}–{core.GRID_HOURS.stop - 1}; "
            "для часов вне его передайте allow_outside_hours=true после подтверждения")



class SlotsIn(core.ApiIn):
    text: str | None = None                    # «окна для Наташи и Сэма в Жизни насекомых»
    mode: Literal["all", "subset", "max_free"] | None = None
    project: str | None = None
    policy: str | None = None                  # ключ из window_policy.POLICIES
    participants: list[int] | None = None      # None — вся труппа без архивных
    optional_participants: list[int] | None = None   # желательные: не блокируют окно
    date_from: str | None = None               # YYYY-MM-DD
    date_to: str | None = None
    min_hours: int = Field(default=2, ge=1, le=12)
    day_start: int = Field(default=8, ge=0, le=23)
    # граница «до», не включается: 24 — конец последней клетки 23:00–24:00
    day_end: int = Field(default=24, ge=1, le=24)
    limit: int = Field(default=3, ge=1, le=20)
    block_events: bool = True                  # вычитать занятость из календаря
    venue: str | None = None                   # код пространства для аренды

    @field_validator("mode", "policy", mode="before")
    @classmethod
    def _normalize_choice(cls, value: Any) -> Any:
        """«MAX_FREE» и « show » — то же самое; пустая строка — не выбрано."""
        if isinstance(value, str):
            value = value.strip().lower()
            return value or None
        return value

    @field_validator("policy")
    @classmethod
    def _known_policy(cls, value: str | None) -> str | None:
        # раньше неизвестная политика молча становилась репетицией (smoke S-9)
        if value is not None and value not in POLICIES:
            raise ValueError(f"unknown_policy: допустимо {', '.join(sorted(POLICIES))}")
        return value



def _resolve_subject(conn, text: str) -> dict:
    """Фраза человека → кто участвует и в каком режиме (docs/architecture/41)."""
    rows = conn.execute("""
        select p.id, p.display_name, a.alias, a.alias_kind, a.project_id
        from participant p left join person_alias a on a.participant_id = p.id
        where p.status <> 'archived' order by p.id""").fetchall()

    people: dict[int, object] = {}
    for row in rows:
        person = people.setdefault(row["id"], SubjectPerson(
            id=row["id"], name=row["display_name"] or ""))
        if row["alias"]:
            person.aliases.append((row["alias"], row["alias_kind"], row["project_id"]))

    projects = [SubjectProject(id=r["id"], name=r["name"])
                for r in conn.execute("select id, name from project").fetchall()]

    subject = parse_subject(text, list(people.values()), projects)
    return {
        "mode": subject.mode,
        "participant_ids": subject.participant_ids,
        "project_ids": subject.project_ids,
        "unresolved": subject.unresolved,
        "confidence": round(subject.confidence, 2),
        "hint": subject.hint,
    }



def _annotate_preferred(items: list[dict], preferred: dict[int, set], people: dict) -> None:
    """Помечает окна, где кому-то час неудобен, но свободен (C-4)."""
    for item in items:
        day = item.get("day")
        start, end = int(item.get("from_hour", 0)), int(item.get("to_hour", 0))
        names = [people.get(pid, str(pid)) for pid, hours in preferred.items()
                 if any(d.isoformat() == day and start <= h < end for d, h in hours)]
        if names:
            item["preferred"] = names



@router.post("/tools/schedule/slots")
def schedule_slots(body: SlotsIn,
                   who: dict = Depends(core.require("schedule.slots"))) -> dict:
    """
    Поиск общих окон (docs/architecture/17 §3.2, docs/architecture/40). Считает код, не модель.

    Пустая клетка = «не отмечено» и не блокирует окно, но человек попадает
    в список «требует подтверждения».
    """
    if body.participants == []:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "participants_empty: передайте список людей или null")

    today = datetime.now(core.TZ).date()
    date_from = (datetime.strptime(body.date_from, "%Y-%m-%d").date()
                 if body.date_from else today)
    date_to = (datetime.strptime(body.date_to, "%Y-%m-%d").date()
               if body.date_to else date_from + timedelta(days=6))
    if date_to < date_from:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "date_to_earlier_than_date_from")
    if (date_to - date_from).days > 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "range_too_long")

    with core.pool.connection() as conn:
        # разбор субъекта запроса: роли, псевдонимы, «для всех», «максимум свободных»
        explicit_ids = list(body.participants or [])
        resolved = None
        if body.text and not body.participants:
            resolved = _resolve_subject(conn, body.text)
            if resolved["mode"] == "subset" and resolved["participant_ids"]:
                body.participants = resolved["participant_ids"]

        if body.participants:
            rows = conn.execute(
                "select id, display_name, travel_minutes from participant where id = any(%s)",
                (body.participants,)).fetchall()
        else:
            # для планирования важны все, кто есть в труппе, а не только те,
            # кто уже зарегистрировался в боте: у pending-людей есть занятость
            rows = conn.execute(
                "select id, display_name, travel_minutes from participant "
                "where status <> 'archived' order by display_name").fetchall()

        people = {r["id"]: r["display_name"] for r in rows}
        # 0 минут — законное значение («живёт в театре»), а не «не задано» (C-13)
        travel = {r["id"]: (60 if r["travel_minutes"] is None else int(r["travel_minutes"]))
                  for r in rows}
        # битый id — ошибка клиента, а не «в труппе никого нет» (smoke S-14)
        unknown = sorted(set(explicit_ids) - set(people))
        if unknown:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"unknown_participants: {unknown}")
        if not people:
            return {"ok": True, "windows": [], "people": {}, "reason": "no_participants"}

        cells = conn.execute(
            """select participant_id, day, hour, is_free, kind, at_theatre
               from availability_hour
               where participant_id = any(%s) and day between %s and %s""",
            (list(people), date_from, date_to)).fetchall()

    theatre_hours: dict[int, set] = {}
    for cell in cells:
        if cell["at_theatre"]:
            theatre_hours.setdefault(cell["participant_id"], set()).add(
                (cell["day"], cell["hour"]))

    marks: dict[int, dict] = {pid: {} for pid in people}
    # C-4 (решение 28.10.2026): «удобно» — это свободен, но часам не хочется мешать.
    # Храним отдельно, чтобы пометить окно в карточке, а не выкинуть его.
    preferred: dict[int, set[tuple]] = {}
    for cell in cells:
        # «не уверен» — это не свободен: окно на таком часе строить нельзя
        marks[cell["participant_id"]][(cell["day"], cell["hour"])] = (
            bool(cell["is_free"]) and cell["kind"] != "uncertain")
        if cell["kind"] == "preferred":
            preferred.setdefault(cell["participant_id"], set()).add(
                (cell["day"], cell["hour"]))

    days = [date_from + timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)]

    # события в календаре занимают людей не меньше, чем их собственные отметки
    events_used = 0
    blocked = 0
    if body.block_events:
        raw_events = core.fetch_events(date_from, date_to)
        events_used = len(raw_events)
        blocks = blocked_hours(raw_events, only_days=set(days))
        if blocks:
            marks, blocked = apply_blocks(marks, blocks)

    windows = find_windows(marks, days, min_hours=body.min_hours,
                           day_start=body.day_start, day_end=body.day_end,
                           limit=body.limit)

    if body.policy:
        kind = body.policy
        # аренда с персоналом театра (сопровождение, администратор события)
        if kind == "rental" and body.participants:
            kind = "rental_staff"
        policy = policy_for(kind)
        venue_info = {"calendar": "", "busy": 0, "applied": False}
        required = body.participants or list(people)
        optional = [pid for pid in (body.optional_participants or [])
                    if pid not in required] or [pid for pid in people if pid not in required]
        found = find_policy_windows(marks, days, required=required, optional=optional,
                                    policy=policy, min_hours=body.min_hours,
                                    day_start=body.day_start, day_end=body.day_end,
                                    limit=body.limit, travel=travel,
                                    theatre_hours=theatre_hours)
        if policy.kind in ("rental", "rental_staff"):
            # Область субаренды: окно — это свободный зал (docs/architecture/40 §4).
            # По умолчанию аренда считается по студии целиком (одно помещение);
            # конкретное пространство — только если его явно попросили.
            if body.venue:
                code = body.venue.strip().lower()
                busy, venue_info = core.venue_busy_for(
                    code, date_from, date_to,
                    raw_events=core.fetch_events(date_from, date_to))
            else:
                code = "studio"
                studio_events = core.fetch_events(date_from, date_to)
                busy = busy_for_studio(studio_events) if studio_events else []
                venue_info = {"venue": "studio", "name": "Студия",
                              "source": "events", "applied": bool(studio_events)}
                if not studio_events:
                    venue_info["reason"] = "no_events"
            if venue_info.get("applied"):
                venue_info["busy"] = len(busy)

            # Данных о зале нет — окна не выдумываем. Пустой ответ с причиной
            # честнее окна «08:00–10:00»: сдать зал, когда неизвестно, занят ли он,
            # нельзя (раньше именно это и происходило).
            if not venue_info.get("applied"):
                return {
                    "ok": True, "mode": "venue", "policy": policy.kind,
                    "policy_title": f"аренда ({venue_info.get('name') or code})",
                    "windows": [], "reason": "venue_unknown",
                    "text": [],
                    "venue": venue_info,
                    "resolved": resolved,
                    "meta": {"from": date_from.isoformat(), "to": date_to.isoformat(),
                             "days": len(days), "min_hours": body.min_hours,
                             "participants": 0, "cells": len(cells),
                             "events": events_used, "blocked_hours": blocked,
                             "venue": venue_info, "reason": "venue_unknown"},
                }

            free_venue = find_venue_windows(busy, days, min_hours=body.min_hours,
                                            day_start=body.day_start,
                                            day_end=body.day_end,
                                            limit=max(body.limit, 10))
            if policy.kind == "rental":
                # персонала нет: результат — свободные окна зала
                venue_text = [
                    f"{['пн','вт','ср','чт','пт','сб','вс'][w.day.weekday()]} "
                    f"{w.day:%d.%m} {w.from_hour:02d}:00–{w.to_hour:02d}:00 ({w.hours} ч)"
                    for w in free_venue]
                return {
                    "ok": True, "mode": "venue", "policy": "rental",
                    "policy_title": f"аренда ({venue_info.get('name') or code})",
                    "windows": [w.as_dict() for w in free_venue],
                    "text": venue_text,
                    "venue": venue_info,
                    "resolved": resolved,
                    "meta": {"from": date_from.isoformat(), "to": date_to.isoformat(),
                             "days": len(days), "min_hours": body.min_hours,
                             "participants": 0, "cells": len(cells),
                             "events": events_used, "blocked_hours": blocked,
                             "venue": venue_info},
                }

            # с персоналом: пересекаем окна людей со свободным залом
            people_windows = [(w.day, w.from_hour, w.to_hour) for w in found]
            allowed = {(w.day, w.from_hour, w.to_hour)
                       for w in intersect_venue(free_venue, people_windows)}
            found = [w for w in found
                     if any(w.day == day and start <= w.from_hour and end >= w.to_hour
                            for day, start, end in allowed)]

        lines = []
        for window in found:
            weekday = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][window.day.weekday()]
            line = (f"{weekday} {window.day:%d.%m} {window.from_hour:02d}:00–"
                    f"{window.to_hour:02d}:00 ({window.hours} ч)")
            if window.joining:
                who = ", ".join(people.get(pid, str(pid)) for pid, _ in window.joining)
                line += f", позже подойдут: {who}"
            if window.absent:
                who = ", ".join(people.get(pid, str(pid)) for pid in window.absent)
                line += f", не смогут: {who}"
            lines.append(line)
        windows_out = [w.as_dict() for w in found]
        _annotate_preferred(windows_out, preferred, people)
        # пометка «удобнее иначе» дублируется в человеческом тексте
        for line, item in zip(lines, windows_out, strict=False):
            if item.get("preferred"):
                line += ", удобнее иначе: " + ", ".join(item["preferred"])
        return {
            "ok": True,
            "mode": "policy",
            "policy": policy.kind,
            "policy_title": policy.title,
            "travel_minutes": travel,
            "theatre_hours_known": sum(len(v) for v in theatre_hours.values()),
            "venue": venue_info,
            "windows": windows_out,
            "text": lines,
            "people": people,
            "resolved": resolved,
            "meta": {"from": date_from.isoformat(), "to": date_to.isoformat(),
                     "days": len(days), "min_hours": body.min_hours,
                     "required": len(required), "optional": len(optional),
                     "participants": len(people), "cells": len(cells),
                     "events": events_used, "blocked_hours": blocked,
                     "venue": venue_info},
        }

    if (body.mode or (resolved or {}).get("mode")) == "max_free":
        ranked = rank_hours(marks, days, day_start=body.day_start,
                            day_end=body.day_end, limit=max(body.limit, 3))
        return {
            "ok": True, "mode": "max_free",
            "hours": [r.as_dict() for r in ranked],
            "text": [f"{['пн','вт','ср','чт','пт','сб','вс'][r.day.weekday()]} "
                     f"{r.day:%d.%m} {r.hour:02d}:00 — свободны {r.free_cnt} "
                     f"из {r.marked_cnt}"
                     for r in ranked],
            "people": people,
            "resolved": resolved,
            "meta": {"from": date_from.isoformat(), "to": date_to.isoformat(),
                     "days": len(days), "participants": len(people), "cells": len(cells)},
        }

    unknown_ids = sorted({pid for w in windows for pid in w.unknown})
    windows_out = [w.as_dict() for w in windows]
    _annotate_preferred(windows_out, preferred, people)
    texts = [describe(w, people) for w in windows]
    for index, item in enumerate(windows_out):
        if item.get("preferred"):
            texts[index] += ", удобнее иначе: " + ", ".join(item["preferred"])
    return {
        "ok": True,
        "windows": windows_out,
        "text": texts,
        "people": people,
        "unknown": [{"id": pid, "name": people.get(pid, str(pid))} for pid in unknown_ids],
        "resolved": resolved,
        "meta": {"from": date_from.isoformat(), "to": date_to.isoformat(),
                 "days": len(days), "min_hours": body.min_hours,
                 "participants": len(people),
                 "cells": len(cells), "events": events_used,
                 "blocked_hours": blocked},
    }



class AvailParseIn(core.ApiIn):
    text: str



@router.post("/tools/availability/parse")
def availability_parse(body: AvailParseIn, who: dict = Depends(core.current)) -> dict:
    return parse_availability(body.text, today=datetime.now(core.TZ).date())



class IntervalIn(core.ApiIn):
    text: str



@router.post("/tools/availability/parse-interval")
def availability_parse_interval(body: IntervalIn, who: dict = Depends(core.current)) -> dict:
    """Интервал часов без даты: форма занятости уже знает день.

    Нужен отдельный маршрут: `/tools/availability/parse` требует дату в тексте,
    и «с 10 до 14» в форме возвращало need_date — бот зацикливался на «не разобрал время».
    """
    return parse_interval(body.text)



class AvailCommitIn(core.ApiIn):
    chat_id: int
    day: str
    start: int
    end: int
    kind: str = "busy"
    raw_text: str | None = None
    allow_outside_hours: bool = False



@router.post("/tools/availability/commit")
def availability_commit(body: AvailCommitIn,
                        who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """Отметка интервалом часов — канон записи для чата (З1, FR-C2).

    Пишет через `availability_store.write_range`: тот же путь, что карточка дня
    (`_save_week`) и сетка. Раньше здесь был свой `insert` с другим `is_free`,
    и «не уверен» из формы читался обратно как занятость.
    """
    _check_hours(body.start, body.end, body.allow_outside_hours)
    with core.pool.connection() as conn:
        d = datetime.strptime(body.day, "%Y-%m-%d").date()
        availability_store.write_range(conn, who["id"], d, body.start, body.end,
                                       body.kind, raw_text=body.raw_text)
        core.audit(conn, "human", who.get("id"), "availability.set", "availability_hour",
              f"{who['id']}:{body.day}", after={"start": body.start, "end": body.end,
                                                "kind": body.kind},
              source={"raw_text": body.raw_text})
    return {"ok": True, "day": body.day, "hours": f"{body.start:02d}–{body.end:02d}"}



@router.get("/tools/availability/summary")
def availability_summary(days: int = Query(default=7, ge=1, le=90),
                         who: dict = Depends(core.require('schedule.read.all'))) -> dict:
    '''Сводка свободных по дням для организатора (FR-C5).

    Считаем в границах рабочего дня 08–23 — как сетка и лист; отметки вне его не
    теряем, а показываем отдельным счётчиком. Одного `is_free` мало: «не уверен»
    лежит с `is_free = true`, но свободным не считается — так же думает поиск
    окон. Дни без отметок остаются в ответе с нулями: раньше такой день просто
    пропадал из списка, и сводка выглядела сломанной (обращение №11).
    '''
    today = datetime.now(core.TZ).date()
    first, last = core.GRID_HOURS.start, core.GRID_HOURS.stop
    with core.pool.connection() as conn:
        rows = conn.execute('''
            select day,
                   count(*) filter (where hour >= %s and hour < %s) as known_cnt,
                   count(*) filter (where hour >= %s and hour < %s
                                      and is_free and kind <> 'uncertain') as free_cnt,
                   count(distinct participant_id)
                       filter (where hour >= %s and hour < %s) as people_cnt,
                   count(*) filter (where hour < %s or hour >= %s) as outside_hours
            from availability_hour
            where day between %s and %s
            group by day''',
            (first, last, first, last, first, last, first, last,
             today, today + timedelta(days=days - 1))).fetchall()
    by_day = {row['day']: row for row in rows}
    data = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        row = by_day.get(day)
        data.append({
            'day': day.isoformat(),
            'free_cnt': row['free_cnt'] if row else 0,
            'known_cnt': row['known_cnt'] if row else 0,
            'people_cnt': row['people_cnt'] if row else 0,
            'outside_hours': row['outside_hours'] if row else 0,
        })

    # покрытие (FR-C7): свод врёт молча, если отметилась половина группы —
    # пустая клетка читается как «свободен». Поэтому считаем и отдаём вердикт.
    with core.pool.connection() as conn:
        # знаменатель — активные участники студии, а не «те, у кого сошлись роли
        # и права»: у части людей строка participant_role появилась позже, и такой
        # счёт давал «отметились 9 из 4» на живых данных
        expected = conn.execute(
            "select count(*) as cnt from participant where status = 'active'").fetchone()['cnt']
        # числитель — тоже только активные: ушедшие в архив отметки не считаем,
        # иначе покрытие превышает 100%
        marked = conn.execute(
            """select count(distinct a.participant_id) as cnt
               from availability_hour a
               join participant p on p.id = a.participant_id
               where p.status = 'active' and a.day between %s and %s""",
            (today, today + timedelta(days=days - 1))).fetchone()['cnt']
        pending = conn.execute(
            "select count(*) as cnt from participant where status = 'pending'").fetchone()['cnt']
    week = coverage(expected, marked, not_connected=pending)
    return {'ok': True, 'days': days, 'data': data,
            'coverage': {'expected': week.expected, 'marked': week.marked,
                         'not_connected': week.not_connected,
                         'ratio': week.ratio, 'trustworthy': week.trustworthy,
                         'title': week_title(today, days), 'line': week.line()}}



class WeekDayIn(core.ApiIn):
    """Один день недели: состояние, две границы (необязательные), комментарий."""

    day: str
    status: Literal["free", "busy", "uncertain"]
    from_time: str | None = None
    to_time: str | None = None
    note: str | None = None



class WeekIn(core.ApiIn):
    """Неделя занятости: 7 карточек дня. `start` — любой день недели, приводим к понедельнику."""

    start: str | None = None
    days: list[WeekDayIn] = Field(min_length=1, max_length=core.WEEK_DAYS)



def _week_start(value: str | None) -> date:
    """Понедельник недели, к которой относится `value` (по умолчанию — сегодня)."""
    if not value:
        base = datetime.now(core.TZ).date()
    else:
        try:
            base = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ApiError(status.HTTP_400_BAD_REQUEST,
                           "Дата должна быть в формате ГГГГ-ММ-ДД", code="bad_date") from None
    return base - timedelta(days=base.weekday())



def _card_out(day: date, card: DayCard | None, female: bool = False) -> dict:
    """Карточка дня → ответ API. Неотмеченный день отдаётся явно, а не пропадает."""
    if card is None:
        return {'day': day.isoformat(), 'marked': False, 'status': None,
                'from': None, 'to': None, 'note': '', 'text': None, 'exact': True}
    return {'day': day.isoformat(), 'marked': True, 'status': card.status,
            'from': to_minutes_text(card.from_min), 'to': to_minutes_text(card.to_min),
            'note': card.note, 'text': to_text(card, female=female), 'exact': card.exact}



def _read_week(target: int, first: date) -> tuple[list[tuple[date, DayCard | None]], bool]:
    """
    Неделя из базы: карточки по дням и род человека.

    Объявленную карточку берём из `raw_text` (там минуты), из часов собираем только
    то, что пришло другими путями — импортом, сеткой, формой в чате.
    """
    last = first + timedelta(days=core.WEEK_DAYS - 1)
    with core.pool.connection() as conn:
        person = conn.execute("select write_gender from participant where id = %s",
                              (target,)).fetchone()
        rows = conn.execute("""
            select day, hour, kind, raw_text
            from availability_hour
            where participant_id = %s and day between %s and %s
            order by day, hour""", (target, first, last)).fetchall()
    female = bool(person and (person["write_gender"] or "m") == "f")

    by_day: dict[date, dict[int, str]] = {}
    texts: dict[date, str] = {}
    for row in rows:
        by_day.setdefault(row["day"], {})[row["hour"]] = row["kind"]
        if row["raw_text"] and row["day"] not in texts:
            texts[row["day"]] = row["raw_text"]

    cards: list[tuple[date, DayCard | None]] = []
    for offset in range(core.WEEK_DAYS):
        day = first + timedelta(days=offset)
        declared = parse(texts[day]) if day in texts else None
        cards.append((day, declared if declared is not None else from_hours(by_day.get(day) or {})))
    return cards, female



def _week_title(first: date) -> str:
    """«02 — 08 ноября» или «30 ноября — 06 декабря», если неделя переходит месяц."""
    last = first + timedelta(days=core.WEEK_DAYS - 1)
    if first.month == last.month:
        return f"{first.day:02d} — {last.day:02d} {core.MONTHS_GENITIVE[first.month - 1]}"
    return (f"{first.day:02d} {core.MONTHS_GENITIVE[first.month - 1]} — "
            f"{last.day:02d} {core.MONTHS_GENITIVE[last.month - 1]}")



def _week_payload(target: int, first: date) -> dict:
    """Неделя карточками для API и Mini App: подписи дней и канонический текст."""
    cards, female = _read_week(target, first)
    days = []
    for day, card in cards:
        item = _card_out(day, card, female)
        item["weekday"] = core.WEEKDAY_SHORT[day.weekday()]
        item["short"] = day.strftime("%d.%m")
        days.append(item)
    return {"ok": True, "start": first.isoformat(), "title": _week_title(first),
            "participant_id": target, "female": female, "days": days}



@router.get("/tools/availability/week")
def availability_week(start: str | None = None, participant_id: int | None = None,
                      who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """
    Неделя занятости карточками дня (FR-C9).

    Читаем часы из `availability_hour` и собираем обратно карточки: «занят до 18:30»,
    «свободен с 19:00», «занят с 16 до 20». Чужую неделю видит только тот, у кого
    есть `schedule.read.all` — как в сводке (FR-C5).
    """
    target = participant_id or who["id"]
    if target != who["id"]:
        caps = who.get("caps") or []
        if "*" not in caps and "schedule.read.all" not in caps:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "capability required: schedule.read.all")

    return _week_payload(target, _week_start(start))



def _prepare_week(days_in: list, first: date) -> list[tuple[date, DayCard]]:
    """Проверить дни недели и собрать карточки. Кривой ввод — 400, а не догадка."""
    allowed = {first + timedelta(days=offset) for offset in range(core.WEEK_DAYS)}
    prepared: list[tuple[date, DayCard]] = []
    for item in days_in:
        try:
            day = datetime.strptime(item.day, "%Y-%m-%d").date()
        except ValueError:
            raise ApiError(status.HTTP_400_BAD_REQUEST,
                           "Дата должна быть в формате ГГГГ-ММ-ДД", code="bad_date") from None
        if day not in allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "day_outside_week")
        try:
            card = DayCard(
                item.status,
                None if item.from_time in (None, "") else parse_clock(item.from_time),
                None if item.to_time in (None, "") else parse_clock(item.to_time),
                (item.note or "").strip(),
            )
        except DayCardError as exc:
            raise ApiError(status.HTTP_400_BAD_REQUEST, str(exc), code="day_card_invalid") from exc
        prepared.append((day, card))
    return prepared



def _save_week(target: int, prepared: list[tuple[date, DayCard]], actor_id: int) -> int:
    """
    Записать карточки недели в базу и вернуть число часов.

    Каждая карточка материализуется на весь день: интервал — выбранным состоянием,
    остальные часы — противоположным. Поэтому «свободен» и «данных нет» остаются
    разными вещами, а поиск окон не считает свободным того, кто не отмечался.
    Канонический текст карточки кладём в `raw_text` — из него же читаем обратно.

    Своего SQL здесь нет: часы пишет `availability_store.write_hours`, один путь
    записи на все входы (З1). Вторая запись в этом месте и была причиной того,
    что неделя и отметка по часам расходились в `is_free`.
    """
    hours_written = 0
    with core.pool.connection() as conn:
        person = conn.execute("select write_gender from participant where id = %s",
                              (target,)).fetchone()
        female = bool(person and (person["write_gender"] or "m") == "f")
        for day, card in prepared:
            text = to_text(card, female=female)
            hours = to_hours(card)
            hours_written += availability_store.write_hours(conn, target, day, hours,
                                                            raw_text=text)
            core.audit(conn, "human", actor_id, "availability.week", "availability_hour",
                  f"{target}:{day.isoformat()}",
                  after={"status": card.status, "from": to_minutes_text(card.from_min),
                         "to": to_minutes_text(card.to_min), "hours": len(hours)},
                  source={"text": text})
    return hours_written



@router.post("/tools/availability/week")
def availability_week_set(body: WeekIn,
                          who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """Записать неделю карточками дня (FR-C9). Общая логика — `_week_saved`."""
    return _week_saved(who["id"], body, who["id"])


def _week_saved(target: int, body: WeekIn, actor_id: int) -> dict:
    """Запись недели — один обработчик на все входы (З1).

    Через него идут чат (`/tools/availability/week`), форма дня в боте и
    Mini App (`/webapp/availability/week`): подготовка карточек и запись часов
    те же, отличается только способ авторизации.
    """
    first = _week_start(body.start)
    prepared = _prepare_week(body.days, first)
    hours = _save_week(target, prepared, actor_id)
    return {"ok": True, "start": first.isoformat(), "days": len(prepared), "hours": hours,
            "data": [_card_out(day, card) for day, card in prepared]}



class TemplateDayIn(core.ApiIn):
    weekday: int = Field(ge=0, le=6)
    status: Literal["free", "busy", "uncertain"]
    from_time: str | None = None
    to_time: str | None = None
    note: str | None = None



class TemplateIn(core.ApiIn):
    days: list[TemplateDayIn] = Field(default_factory=list, max_length=7)



class TemplateFromWeekIn(core.ApiIn):
    start: str | None = None



class TemplateApplyIn(core.ApiIn):
    start: str | None = None
    overwrite: bool = False      # перезаписать уже отмеченные дни
    apply: bool = True



def _template_out(item: TemplateDay, female: bool) -> dict:
    return {"weekday": item.weekday, "status": item.card.status,
            "from": to_minutes_text(item.card.from_min),
            "to": to_minutes_text(item.card.to_min), "note": item.card.note,
            "text": to_text(item.card, female=female)}



def _template_load(participant_id: int) -> list[TemplateDay]:
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select weekday, status, from_min, to_min, note
            from availability_template where participant_id = %s order by weekday""",
            (participant_id,)).fetchall()
    return [TemplateDay(row["weekday"], DayCard(row["status"], row["from_min"],
                                                row["to_min"], row["note"] or ""))
            for row in rows]



def _template_save(participant_id: int, items: list[TemplateDay], actor_id: int) -> None:
    """Полная замена шаблона: удаляем и пишем заново — иначе пустые дни остаются висеть."""
    import json as _json

    with core.pool.connection() as conn:
        conn.execute("delete from availability_template where participant_id = %s",
                     (participant_id,))
        for item in items:
            card = item.card
            conn.execute("""
                insert into availability_template
                  (participant_id, weekday, status, from_min, to_min, note)
                values (%s,%s,%s,%s,%s,%s)""",
                         (participant_id, item.weekday, card.status, card.from_min,
                          card.to_min, card.note or None))
        core.audit(conn, "human", actor_id, "availability.template", "availability_template",
              str(participant_id),
              after={"weekdays": [item.weekday for item in items]},
              source={"texts": _json.dumps([to_text(item.card) for item in items],
                                           ensure_ascii=False)})



def _template_items(days_in: list) -> list[TemplateDay]:
    items: list[TemplateDay] = []
    seen: set[int] = set()
    for item in days_in:
        if item.weekday in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "weekday_duplicated")
        seen.add(item.weekday)
        try:
            card = DayCard(
                item.status,
                None if item.from_time in (None, "") else parse_clock(item.from_time),
                None if item.to_time in (None, "") else parse_clock(item.to_time),
                (item.note or "").strip(),
            )
        except DayCardError as exc:
            raise ApiError(status.HTTP_400_BAD_REQUEST, str(exc), code="day_card_invalid") from exc
        items.append(TemplateDay(item.weekday, card))
    return sorted(items, key=lambda entry: entry.weekday)



def _gender(participant_id: int) -> bool:
    with core.pool.connection() as conn:
        row = conn.execute("select write_gender from participant where id = %s",
                           (participant_id,)).fetchone()
    return bool(row and (row["write_gender"] or "m") == "f")



@router.get("/tools/availability/template")
def availability_template(who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """Своя «обычная неделя»: состояние каждого дня недели (FR-C10). Канон для обоих входов."""
    return _template_payload(who["id"])


def _template_payload(target: int) -> dict:
    """Шаблон человека как ответ API — один набор полей для чата и Mini App."""
    female = _gender(target)
    items = _template_load(target)
    return {"ok": True, "filled": len(items),
            "days": [_template_out(item, female) for item in items]}



@router.post("/tools/availability/template")
def availability_template_save(body: TemplateIn,
                               who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """Сохранить шаблон целиком. Пустой список — стереть шаблон."""
    items = _template_items(body.days)
    _template_save(who["id"], items, who["id"])
    female = _gender(who["id"])
    return {"ok": True, "filled": len(items),
            "days": [_template_out(item, female) for item in items]}



@router.post("/tools/availability/template/from-week")
def availability_template_from_week(body: TemplateFromWeekIn,
                                    who: dict = Depends(
                                        core.require("schedule.availability.self"))) -> dict:
    """Запомнить текущую неделю как обычную (З8): ровно то, что человек уже отметил."""
    return _template_from_week(who["id"], body.start, who["id"])


def _template_from_week(target: int, start: str | None, actor_id: int) -> dict:
    """Неделя становится шаблоном: сохранение одно, входов два (tools и Mini App)."""
    first = _week_start(start)
    cards, female = _read_week(target, first)
    marked = {day: card for day, card in cards if card is not None}
    if not marked:
        return {"ok": False, "error": "nothing_to_save",
                "detail": "на этой неделе нет отмеченных дней"}
    items = template_from_week(marked)
    _template_save(target, items, actor_id)
    return {"ok": True, "filled": len(items),
            "days": [_template_out(item, female) for item in items]}



def _template_apply(target: int, body: TemplateApplyIn, actor_id: int) -> dict:
    """Развернуть шаблон на неделю. Без `overwrite` отмеченные дни не трогаем."""
    first = _week_start(body.start)
    template = _template_load(target)
    if not template:
        return {"ok": False, "error": "no_template",
                "detail": "обычная неделя ещё не сохранена"}
    cards, female = _read_week(target, first)
    write, skipped = template_fill(first, template, dict(cards), body.overwrite)
    preview = [{"day": day.isoformat(), "text": to_text(card, female=female)}
               for day, card in sorted(write.items())]
    if not body.apply:
        return {"ok": True, "applied": False, "start": first.isoformat(),
                "days": preview, "skipped": [day.isoformat() for day in skipped]}
    hours = _save_week(target, sorted(write.items()), actor_id)
    return {"ok": True, "applied": True, "start": first.isoformat(), "hours": hours,
            "days": preview, "skipped": [day.isoformat() for day in skipped]}



@router.post("/tools/availability/template/apply")
def availability_template_apply(body: TemplateApplyIn,
                                who: dict = Depends(
                                    core.require("schedule.availability.self"))) -> dict:
    """Заполнить неделю по «обычной неделе» (FR-C10)."""
    return _template_apply(who["id"], body, who["id"])



def _webapp_caps(who: dict) -> list[str]:
    with core.pool.connection() as conn:
        return core.resolve_caps(conn, who["id"])



def _webapp_self(who: dict) -> dict:
    """Право отметить себя: та же проверка, что в чате, без второго набора правил."""
    caps = _webapp_caps(who)
    if "*" not in caps and "schedule.availability.self" not in caps:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "capability required: schedule.availability.self")
    return who



@router.get("/webapp/slots/people")
def webapp_slots_people(who: dict = Depends(core.webapp_actor)) -> dict:
    """Кого можно выбрать в критериях: труппа и организаторы без архива.

    Рядом — `my_disciplines`: дисциплины самого актора. Экран «Поиск окон» показывает
    выбор дисциплины для тренинга, и без этого списка тренинг мастера всегда уходил бы
    в ветку «дисциплина не указана — добавлены все педагоги» (140 §4.2). У администратора
    и организатора список пуст — селектор не рисуется, поведение прежнее.
    """
    viewer = core._webapp_require(who, "schedule.slots")
    with core.pool.connection() as conn:
        rows = conn.execute(
            "select id, display_name from participant "
            "where status <> 'archived' order by display_name").fetchall()
        disciplines = core.disciplines_out(conn, viewer["id"])
    return {"ok": True, "people": [dict(row) for row in rows],
            "my_disciplines": disciplines}



class WebAppSlotsIn(core.ApiIn):
    date_from: str | None = None
    date_to: str | None = None
    min_hours: int = Field(default=2, ge=1, le=12)
    participants: list[int] = Field(default_factory=list)
    optional: list[int] = Field(default_factory=list)
    # Тип события задаёт строгость окна (docs/architecture/42). Экран присылает
    # его всегда; по умолчанию — репетиция как самый частый случай.
    policy: str | None = None



@router.post("/webapp/slots/find")
def webapp_slots_find(body: WebAppSlotsIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Окна по критериям. Желательные не блокируют окно, а попадают в пояснение.

    Две области одного действия (С6, docs/architecture/40 §4): у событий студии
    окно строится по людям, у аренды — по залу, и людей там может не быть вовсе.
    Поэтому пустой список участников — ошибка только для области участников.
    """
    viewer = core._webapp_require(who, "schedule.slots")
    kind = (body.policy or "rehearsal").strip().lower()
    people = list(dict.fromkeys(body.participants + body.optional))
    if not people and kind not in ("rental", "rental_staff"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "participants_empty")
    result = schedule_slots(SlotsIn(participants=body.participants or people or None,
                                    optional_participants=body.optional,
                                    date_from=body.date_from, date_to=body.date_to,
                                    min_hours=body.min_hours, limit=8,
                                    policy=kind), who=viewer)
    return result



@router.get("/webapp/availability/week")
def webapp_availability_week(start: str | None = None,
                             who: dict = Depends(core.webapp_actor)) -> dict:
    """Своя неделя для Mini App. Право проверяем то же, что в чате."""
    _webapp_self(who)
    return _week_payload(who["id"], _week_start(start))



@router.post("/webapp/availability/week")
def webapp_availability_save(body: WeekIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Сохранение недели из Mini App: тот же обработчик, что в чате (З1)."""
    _webapp_self(who)
    return _week_saved(who["id"], body, who["id"])



@router.get("/webapp/availability/template")
def webapp_availability_template(who: dict = Depends(core.webapp_actor)) -> dict:
    """«Обычная неделя» для страницы: вход тот же канон, что у `/tools/.../template`."""
    _webapp_self(who)
    return _template_payload(who["id"])



@router.post("/webapp/availability/template/from-week")
def webapp_template_from_week(body: TemplateFromWeekIn,
                              who: dict = Depends(core.webapp_actor)) -> dict:
    """Сохранить шаблон недели из Mini App (З8): та же запись, что в чате."""
    _webapp_self(who)
    return _template_from_week(who["id"], body.start, who["id"])



@router.post("/webapp/availability/template/apply")
def webapp_template_apply(body: TemplateApplyIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Заполнить неделю по обычной неделе: отмеченные дни не трогаем без overwrite."""
    _webapp_self(who)
    return _template_apply(who["id"], body, who["id"])



def _schedule_sheet_meta(service) -> tuple[int, str]:
    """sheetId и название листа занятости: нужны, чтобы прятать служебную колонку."""
    meta = service.spreadsheets().get(
        spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
        fields="sheets(properties(sheetId,title))").execute()
    for item in meta.get("sheets") or []:
        props = item.get("properties") or {}
        if not core.SCHEDULE_SHEET_TITLE or props.get("title") == core.SCHEDULE_SHEET_TITLE:
            return int(props["sheetId"]), props["title"]
    raise ValueError("sheet_not_found")



def _hide_column(service, sheet_id: int, column: int) -> None:
    """Скрыть служебную колонку: людям она не нужна, машине — да."""
    service.spreadsheets().batchUpdate(
        spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
        body={"requests": [{"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": column - 1, "endIndex": column},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser"}}]}).execute()



class WeekSheetIn(core.ApiIn):
    start: str | None = None
    apply: bool = False
    force: bool = False          # перезаписать правки человека в листе



def _sheet_sync(target: int, start: str | None, apply: bool, force: bool,
                actor_id: int) -> dict:
    """
    Записать неделю занятости в лист «Расписание студийцы» (FR-C9).

    Порядок как у бюджета: сначала план и расхождения (без `apply` ничего не пишем),
    потом запись каноническим текстом в клетку дня и снимок в скрытую колонку `svoi`.
    После записи адреса перечитываются: «записали» без проверки не считается.

    Привязка не угадывается: строки — из колонки B (там даты), колонка человека — из
    шапки. Не нашли или нашли двоих — возвращаем расхождение, а не пишем наугад.
    """
    if not core.SCHEDULE_SPREADSHEET_ID:
        return {"ok": False, "error": "no_spreadsheet",
                "detail": "SCHEDULE_SPREADSHEET_ID не задан"}

    first = _week_start(start)
    cards, female = _read_week(target, first)
    texts = {day: to_text(card, female=female) for day, card in cards if card is not None}
    if not texts:
        return {"ok": False, "error": "nothing_to_write",
                "detail": "на эту неделю нет отмеченных дней"}

    display, aliases = _sheet_load_person(target)

    service = core._sheets()
    try:
        sheet_id, title = _schedule_sheet_meta(service)
        values = service.spreadsheets().values().get(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
            range=f"'{title}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as e:
        core.log.warning("лист занятости недоступен: %s", e)
        return {"ok": False, "error": "sheet_unavailable",
                "detail": "Лист недоступен: повторите позже"}

    # политика: правку человека не затираем молча — показываем и спрашиваем `force`
    week_days = {day.isoformat() for day in texts}
    conflicts = [row for row in snapshot_mismatches(values, target, display, aliases=aliases)
                 if row["day"] in week_days]
    if conflicts and not force:
        return {"ok": False, "applied": False, "error": "mismatches_present",
                "detail": "в листе есть правки после записи бота — решите, что верно",
                "mismatches": conflicts}

    plan = plan_week(values, texts, target, display, aliases=aliases)
    preview = [{"cell": cell.a1, "text": cell.text} for cell in plan.cells]
    if plan.problems:
        return {"ok": False, "error": "plan_problems", "problems": plan.problems,
                "cells": preview}
    if not apply:
        return {"ok": True, "applied": False, "start": first.isoformat(),
                "sheet": title, "person_column": plan.person_column,
                "service_column": plan.service_column, "cells": preview}

    try:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
            body={"valueInputOption": "RAW",
                  "data": [{"range": f"'{title}'!{cell.a1}", "values": [[cell.text]]}
                           for cell in plan.cells]}).execute()
        header_cell = cell_a1(plan.names_row, plan.service_column)
        if plan.service_column and any(cell.a1 == header_cell for cell in plan.cells):
            _hide_column(service, sheet_id, plan.service_column)
    except GOOGLE_API_ERRORS as e:
        core.log.warning("запись в лист не прошла: %s", e)
        return {"ok": False, "applied": False, "error": "sheet_write_failed",
                "detail": "Лист недоступен: повторите позже", "cells": preview}

    # проверка фактом: читаем те же адреса поимённо (batchGet) и сравниваем.
    # Кусок листа читать нельзя: ответ API обрезает хвост и пустые строки, и адреса
    # в ответе перестают совпадать с тем, что мы записали.
    verified, mismatched = True, []
    try:
        ranges = [f"'{title}'!{cell.a1}" for cell in plan.cells]
        answer = service.spreadsheets().values().batchGet(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID, ranges=ranges).execute()
        for cell, value_range in zip(plan.cells, answer.get("valueRanges", []),
                                     strict=False):
            rows = value_range.get("values") or [[""]]
            got = str((rows[0] or [""])[0] or "")
            if " ".join(got.split()) != " ".join(cell.text.split()):
                verified = False
                mismatched.append({"cell": cell.a1, "expected": cell.text, "got": got})
    except GOOGLE_API_ERRORS as e:
        # адаптер листа: контрольное чтение не прошло — это «не проверили», не «не записали»
        verified = False
        mismatched.append({"error": str(e)[:200]})

    with core.pool.connection() as conn:
        core.audit(conn, "human", actor_id, "availability.sheet", "availability_hour",
              f"{target}:{first.isoformat()}",
              after={"cells": len(plan.cells), "verified": verified,
                     "service_column": plan.service_column})

    return {"ok": True, "applied": True, "verified": verified, "sheet": title,
            "start": first.isoformat(), "cells": preview, "mismatched": mismatched}



@router.post("/tools/availability/sheet")
def availability_sheet(body: WeekSheetIn,
                       who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """Запись недели в лист (FR-C9): см. `_sheet_sync`."""
    return _sheet_sync(who["id"], body.start, body.apply, body.force, who["id"])



@router.post("/webapp/availability/sheet")
def webapp_availability_sheet(body: WeekSheetIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Запись в лист из Mini App: та же логика, что и из чата, без второго набора правил."""
    _webapp_self(who)
    return _sheet_sync(who["id"], body.start, body.apply, body.force, who["id"])



def _sheet_load_values() -> tuple[list[list] | None, str]:
    """Значения общего листа «Расписание студийцы» целиком: (строки, причина отказа).

    Источник — отдельная пара настроек `SCHEDULE_HUMAN_*`: `SCHEDULE_SPREADSHEET_ID` смотрит
    на плоский журнал «Занятость», и загрузка на нём читала бы пустой первый лист
    (docs/development/149 §2.1). Диапазон тот же `A1:AZ3000`, что у остальных читателей листа:
    он растёт вниз, и фиксированные 400 строк из замера стали бы обрезкой.
    """
    if not core.SCHEDULE_HUMAN_SPREADSHEET_ID:
        return None, "no_spreadsheet"
    try:
        service = core._sheets()
        values = service.spreadsheets().values().get(
            spreadsheetId=core.SCHEDULE_HUMAN_SPREADSHEET_ID,
            range=f"'{core.SCHEDULE_HUMAN_SHEET_TITLE}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as e:
        core.log.warning("общий лист «Расписание студийцы» недоступен: %s", e)
        return None, "sheet_unavailable"
    return values, ""


def _sheet_load_person(target: int) -> tuple[str, list[str]]:
    """Имя и псевдонимы человека: по ним ищется колонка в шапке листа (149 D9).

    Ролевые псевдонимы (`alias_kind = 'role'`) не берём: «мастер» — это не имя, и колонку
    по нему искать нельзя.
    """
    with core.pool.connection() as conn:
        person = conn.execute("select display_name from participant where id = %s",
                              (target,)).fetchone()
        rows = conn.execute(
            "select alias from person_alias"
            " where participant_id = %s and alias_kind <> 'role' order by alias",
            (target,)).fetchall()
    return (person["display_name"] if person else ""), [row["alias"] for row in rows]


def _sheet_load_preview(target: int, first: date) -> dict:
    """Предпросмотр недели: где колонка человека, что в листе, что в базе, где расходятся.

    Общий ответ у предпросмотра и загрузки один: загрузка считает состояния тем же путём,
    иначе экран и запись разошлись бы в том, что считать расхождением. Ничего не пишет.
    """
    values, reason = _sheet_load_values()
    if values is None:
        return {"ok": False, "error": reason, "detail": sheet_load.detail(reason)}

    display, aliases = _sheet_load_person(target)
    cards, female = _read_week(target, first)
    days = [first + timedelta(days=offset) for offset in range(core.WEEK_DAYS)]
    db = {day: (to_text(card, female=female) if card is not None else None)
          for day, card in cards}
    preview = sheet_load.build_preview(values, display, aliases, days, db, female=female)
    if not preview["ok"]:
        columns = list(preview.get("columns") or [])
        return {"ok": False, "error": preview["error"],
                "detail": sheet_load.detail(preview["error"], columns),
                "columns": [column_letter(column) for column in columns]}

    out_days = []
    for item in preview["days"]:
        day = date.fromisoformat(item["day"])
        out_days.append({**item, "weekday": core.WEEKDAY_SHORT[day.weekday()]})
    return {"ok": True, "start": first.isoformat(), "title": _week_title(first),
            "source": {"spreadsheet": sheet_load.SOURCE_TITLE,
                       "tab": core.SCHEDULE_HUMAN_SHEET_TITLE},
            "person": {"id": target, "name": display,
                       "column": column_letter(preview["column"])},
            "days": out_days, "counts": preview["counts"]}


@router.get("/webapp/availability/sheet/preview")
def webapp_availability_sheet_preview(start: str | None = None,
                                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Своя колонка из общего листа: предпросмотр недели без записи (Э5, 149 §4.2).

    Только для себя: параметра «чужой человек» здесь нет, право то же, что у отметки недели.
    Неверная дата — 400 `bad_date`, как у недели.
    """
    _webapp_self(who)
    return _sheet_load_preview(who["id"], _week_start(start))


class SheetLoadIn(core.ApiIn):
    """Что загрузить: неделя и дни. Состояния дня считает сервер, клиент присылает даты."""

    start: str | None = None
    days: list[str] = Field(default_factory=list, max_length=core.WEEK_DAYS)


def _sheet_load_days(days: list[str], first: date) -> list[date]:
    """Даты из запроса: порядок сохраняется, дубликаты схлопываются, чужая неделя — 400."""
    if not days:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Выберите хотя бы один день",
                       code="empty_selection")
    allowed = {first + timedelta(days=offset) for offset in range(core.WEEK_DAYS)}
    out: list[date] = []
    for value in days:
        try:
            day = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ApiError(status.HTTP_400_BAD_REQUEST,
                           "Дата должна быть в формате ГГГГ-ММ-ДД", code="bad_date") from None
        if day not in allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "day_outside_week")
        if day not in out:
            out.append(day)
    return out


def _sheet_load_apply(target: int, first: date, requested: list[date],
                      actor_id: int) -> dict:
    """Перенести выбранные дни из листа в базу — одной транзакцией (149 D7, §4.4).

    День заменяется целиком (`delete_day` + `write_hours`): иначе часы 00–07 от старой
    отметки пережили бы загрузку и свободное утро осталось бы враньём. `raw_text` — текст
    листа как написан, поэтому неделя покажет то, что человек видел в таблице. В лист не
    пишем ничего; любое исключение откатывает всю загрузку.
    """
    preview = _sheet_load_preview(target, first)
    if not preview["ok"]:
        return preview

    plan = sheet_load.plan_load(preview["days"], requested)
    skipped = plan["skipped"]
    if not plan["write"]:
        return {"ok": False, "error": "nothing_to_write",
                "detail": sheet_load.detail("nothing_to_write"), "skipped": skipped}

    before = {item["day"]: {"state": item["db_state"], "text": item["db_text"] or ""}
              for item in preview["days"]}
    after = {item["day"]: {"state": item["sheet_state"], "text": item["sheet_text"]}
             for item in preview["days"]}
    done: list[dict] = []
    with core.pool.connection() as conn:
        for item in plan["write"]:
            day = item["day"]
            card = parse(item["text"]) if item["text"] else None
            availability_store.delete_day(conn, target, day)
            if card is not None:
                availability_store.write_hours(
                    conn, target, day, to_hours(card),
                    source=availability_store.SHEET_IMPORT, raw_text=item["text"])
            done.append({"day": day.isoformat(),
                         "action": "загружен" if card is not None else "стёрт",
                         "text": item["text"]})
        keys = [item["day"].isoformat() for item in plan["write"]]
        core.audit(conn, "human", actor_id, "availability.sheet.pull", "availability_hour",
                   f"{target}:{first.isoformat()}",
                   before={key: before[key] for key in keys},
                   after={key: after[key] for key in keys},
                   source={"spreadsheet": sheet_load.SOURCE_TITLE,
                           "tab": core.SCHEDULE_HUMAN_SHEET_TITLE,
                           "column": preview["person"]["column"]})
    return {"ok": True, "applied": True, "start": first.isoformat(),
            "days": done, "skipped": skipped}


@router.post("/webapp/availability/sheet/load")
def webapp_availability_sheet_load(body: SheetLoadIn,
                                   who: dict = Depends(core.webapp_actor)) -> dict:
    """Загрузить выбранные дни своей колонки из общего листа в базу (Э5).

    Дни вне запрошенной недели — 400 до обращения к листу: лишнее не читаем и не пишем.
    """
    _webapp_self(who)
    first = _week_start(body.start)
    requested = _sheet_load_days(body.days, first)
    return _sheet_load_apply(who["id"], first, requested, who["id"])


@router.get("/tools/availability/sheet/mismatches")
def availability_sheet_mismatches(start: str | None = None,
                                  who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """
    Где человек правил лист руками после записи бота (FR-C9).

    Сравниваем клетку дня со снимком `svoi`: пустой снимок — бот сюда не писал,
    расходится — правку надо перенести в базу. Ничего не меняем: расхождение
    показываем, а не подкручиваем.
    """
    if not core.SCHEDULE_SPREADSHEET_ID:
        return {"ok": False, "error": "no_spreadsheet"}
    first = _week_start(start)
    service = core._sheets()
    try:
        _, title = _schedule_sheet_meta(service)
        values = service.spreadsheets().values().get(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
            range=f"'{title}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as e:
        core.log.warning("лист занятости недоступен: %s", e)
        return {"ok": False, "error": "sheet_unavailable",
                "detail": "Лист недоступен: повторите позже"}

    with core.pool.connection() as conn:
        person = conn.execute("select display_name from participant where id = %s",
                              (who["id"],)).fetchone()
    rows = snapshot_mismatches(values, who["id"], person["display_name"] if person else "")
    week = {(first + timedelta(days=offset)).isoformat() for offset in range(core.WEEK_DAYS)}
    return {"ok": True, "start": first.isoformat(),
            "mismatches": [row for row in rows if row["day"] in week]}



class AvailabilityReconcileIn(core.ApiIn):
    start: str | None = None
    participant_id: int | None = None
    apply: bool = False
    accept: bool = False          # принять правки листа в базу



@router.post("/tools/availability/reconcile")
def availability_reconcile(body: AvailabilityReconcileIn,
                           who: dict = Depends(core.require("schedule.availability.self"))) -> dict:
    """
    Сверка занятости: правки человека в листе → база (FR-C9, FR-G13).

    Файл — источник истины: если человек поправил клетку после записи бота, права
    его правка. Но переносим не молча: сначала список «что на что меняем», запись —
    только по `apply` и `accept`. Неразобранный текст не угадываем: он остаётся
    в списке с причиной, а не превращается в догадку.

    Снимок `svoi` обновляется после переноса: иначе то же расхождение всплывало бы
    каждый раз и сверка превратилась бы в шум.
    """
    target = body.participant_id or who["id"]
    if target != who["id"]:
        caps = who.get("caps") or []
        if "*" not in caps and "schedule.read.all" not in caps:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "capability required: schedule.read.all")
    if not core.SCHEDULE_SPREADSHEET_ID:
        return {"ok": False, "error": "no_spreadsheet"}

    first = _week_start(body.start)
    week = {(first + timedelta(days=offset)).isoformat() for offset in range(core.WEEK_DAYS)}
    service = core._sheets()
    try:
        _, title = _schedule_sheet_meta(service)
        values = service.spreadsheets().values().get(
            spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
            range=f"'{title}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as e:
        core.log.warning("лист занятости недоступен: %s", e)
        return {"ok": False, "error": "sheet_unavailable",
                "detail": "Лист недоступен: повторите позже"}

    with core.pool.connection() as conn:
        person = conn.execute("select display_name, write_gender from participant where id = %s",
                              (target,)).fetchone()
    display = person["display_name"] if person else ""
    female = bool(person and (person["write_gender"] or "m") == "f")

    found = [row for row in snapshot_mismatches(values, target, display) if row["day"] in week]
    names_row = find_names_row(values)
    service_column, _ = find_service_column(values, names_row) if names_row else (None, True)
    cards, _ = _read_week(target, first)
    db_text = {day.isoformat(): (to_text(card, female=female) if card else "")
               for day, card in cards}

    items = []
    for row in found:
        card = parse(row["cell"])
        if card is None:
            action, new_text = "стереть", ""
        elif card.status == "uncertain" and card.from_min is None and card.to_min is None:
            action, new_text = "не разобрано", ""
        else:
            action, new_text = "принять", to_text(card, female=female)
        items.append({**row, "db": db_text.get(row["day"], ""),
                      "action": action, "new": new_text})

    if not body.apply:
        return {"ok": True, "applied": False, "start": first.isoformat(), "sheet": title,
                "items": items}
    if items and not body.accept:
        return {"ok": False, "applied": False, "error": "accept_required",
                "detail": "перенос правок в базу требует accept=true", "items": items}

    accepted, problems, snapshots = [], [], []
    with core.pool.connection() as conn:
        for item in items:
            day = date.fromisoformat(item["day"])
            if item["action"] == "не разобрано":
                problems.append({"day": item["day"], "cell": item["cell"],
                                 "reason": "текст не разобран"})
                continue
            if item["action"] == "стереть":
                availability_store.delete_day(conn, target, day)
            else:
                card = parse(item["cell"])
                if card is None:      # «стереть» разобрано выше; здесь карточка обязательна
                    continue
                availability_store.write_hours(conn, target, day, to_hours(card),
                                               source=availability_store.SHEET_IMPORT,
                                               raw_text=item["new"])
            core.audit(conn, "human", who["id"], "availability.reconcile", "availability_hour",
                  f"{target}:{item['day']}",
                  before={"text": item["bot"] or ""},
                  after={"text": item["new"] or "стёрто", "action": item["action"]},
                  source={"sheet": title, "cell": item["cell"]})
            accepted.append(item)
            if service_column:
                # в снимок идёт текст листа, а не канон: канон нормализует род и формат,
                # и тогда сверка снова увидела бы расхождение с собственной записью
                snapshots.append((item["row"], " ".join(item["cell"].split())))

    sheet_error = None
    if snapshots and service_column:
        data = []
        for row_index, text in snapshots:
            existing = ""
            if row_index - 1 < len(values) and len(values[row_index - 1]) >= service_column:
                existing = values[row_index - 1][service_column - 1]
            data.append({"range": f"'{title}'!{cell_a1(row_index, service_column)}",
                         "values": [[merge_snapshot(existing, target, text)]]})
        try:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=core.SCHEDULE_SPREADSHEET_ID,
                body={"valueInputOption": "RAW", "data": data}).execute()
        except GOOGLE_API_ERRORS as e:
            # адаптер листа: строка не дошла — показываем человеку sheet_error
            sheet_error = str(e)[:200]

    return {"ok": True, "applied": True, "start": first.isoformat(), "sheet": title,
            "accepted": accepted, "problems": problems, "sheet_error": sheet_error}



@router.post("/webapp/slots")
def webapp_slots(body: SlotsIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Поиск окон из Mini App: тот же расчёт, другой способ авторизации."""
    return schedule_slots(body, who)



class ClearWeekIn(core.ApiIn):
    week: str = ""          # понедельник недели, YYYY-MM-DD; пусто — текущая



@router.post("/webapp/availability/clear")
def webapp_availability_clear(body: ClearWeekIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Очистить неделю: без этого «заполнить по шаблону» ложится поверх старых отметок."""
    return _clear_week(who["id"], body.week or None, who["id"])


def _clear_week(target: int, week: str | None, actor_id: int) -> dict:
    """Стереть неделю — тот же путь записи, что у отметок (З1, З4).

    `cleared` — это дни-отметки, а не строки `availability_hour`. Экран говорит
    «снято N отметок», и N — число дней, где отметки были: в дне до 16 часов,
    и человек их на экране не считает (Ш-53, docs/reports/72, обращение №39).
    Число часов отдаём отдельно как `hours` — для журнала и разбора.
    """
    first = _week_start(week)
    last = first + timedelta(days=core.WEEK_DAYS - 1)
    with core.pool.connection() as conn:
        # считаем до удаления и в той же транзакции — иначе счёт и удаление разойдутся
        cleared = availability_store.count_days(conn, target, first, last)
        hours = availability_store.delete_week(conn, target, first, last)
        # пустая неделя — тоже событие: в журнале видно, что человек нажимал «Очистить»
        core.audit(conn, "human", actor_id, "availability.clear", "availability_hour",
                   f"{first.isoformat()}:{last.isoformat()}",
                   after={"cleared": cleared, "hours": hours},
                   source={"week": first.isoformat()})
    return {"ok": True, "cleared": cleared, "hours": hours, "start": first.isoformat()}


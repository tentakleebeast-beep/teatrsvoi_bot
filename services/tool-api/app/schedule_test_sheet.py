"""Запись цветовых проб в тестовый лист «Занятость - тест (цвета)» (186 §4.3, AC-10).

Зачем отдельный модуль. Тестовый лист цветов лежал вне сервиса: его наполнял
`scripts/fill_test_schedule.py`, который брал идентификатор файла литералом и сам
создавал страницы (`addSheet`). Критерий AC-10 говорит обратное: тестовый лист
пишется только через ключ реестра `schedule.test`, страница выбирается по шаблону
`MM.YYYY · Ник`, отсутствующая страница — `sheet_ambiguous`, а не создание.

Раскладка `hour_matrix` (186 §3): строка 1 — даты месяца (`A1` = «Час»), колонка A —
часы 08:00…23:00, дальше клетки «дата × час». Проба — это текст клетки и её фон:
цвет и есть то, что проверяется репетицией. Адрес каждой пробы считается здесь,
запись и сверка шапки/свежести — в `sheet_binding`, дедуп — в `row_map`
(`entity_type = color_probe`).

Страницы модуль **не создаёт**: нет страницы — отказ, потому что «создам сам» уже
один раз приводило к записи не туда.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app import core, google_targets, sheet_binding
from app.errors import ApiError
from app.external import GOOGLE_API_ERRORS

KEY = "schedule.test"
ENTITY_TYPE = "color_probe"

#: Статусы пробы и их цвета — те же три, что различает человек: занят, свободен,
#: не уверен. Палитра совпадает с той, что люди видят в тестовом листе.
STATUSES: dict[str, dict[str, Any]] = {
    "busy": {"text": "занят", "color": {"red": 0.92, "green": 0.26, "blue": 0.21}},
    "free": {"text": "свободен", "color": {"red": 0.20, "green": 0.66, "blue": 0.33}},
    "uncertain": {"text": "не уверен", "color": {"red": 0.98, "green": 0.74, "blue": 0.02}},
}

#: Часы раскладки: строка 1 — даты, колонка A — эти подписи.
HOURS = tuple(range(8, 24))

#: Предел чтения страницы: тот же `A1:AZ40`, что в контракте чтения 186 §3.
PAGE_RANGE = "A1:AZ40"

DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")


def month_label(value: str) -> str:
    """`2026-11` или `11.2026` → `11.2026` (как подписаны страницы листа)."""
    text = str(value or "").strip()
    for fmt in ("%Y-%m", "%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%m.%Y")
        except ValueError:
            continue
    raise ApiError(400, "bad_month: ожидается YYYY-MM или MM.YYYY", code="bad_month")


def page_title(pattern: str, month: str, nick: str) -> str:
    """Имя страницы из шаблона `SCHEDULE_TEST_SHEET_PATTERN`.

    Шаблон с подстановками (`{month}`, `{nick}`) разворачивается; шаблон без них
    считается готовым именем страницы — так администратор может закрепить одну
    страницу для репетиции. Пустой шаблон — пустое имя, и его отклонит `pick_sheet`
    (`sheet_ambiguous`), а не «первая страница файла».
    """
    template = str(pattern or "").strip()
    if not template:
        return ""
    if "{" not in template:
        return template
    try:
        return template.format(month=str(month), nick=str(nick))
    except (KeyError, IndexError, ValueError):
        raise ApiError(400, "bad_page_pattern: в шаблоне только {month} и {nick}",
                       code="bad_page_pattern") from None


def parse_day(value: str) -> date:
    """Дата пробы из текста `YYYY-MM-DD` (контракт запроса)."""
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(400, "bad_day: ожидается YYYY-MM-DD", code="bad_day") from None


@dataclass(frozen=True, slots=True)
class Probe:
    """Одна проба: день, час, статус и (необязательно) свой текст клетки."""

    day: date
    hour: int
    status: str = "busy"
    text: str = ""


@dataclass(frozen=True, slots=True)
class PlannedCell:
    """Проба с вычисленным адресом: что писать, куда и как это опознать в `row_map`."""

    a1: str
    text: str
    color: dict
    entity_id: str
    cell_key: str
    sheet_row: int
    sheet_col: int

    @property
    def content_hash(self) -> str:
        """Хеш записи: текст и цвет вместе — цвет у пробы так же важен, как текст."""
        return sheet_binding.content_hash({"text": self.text, "color": self.color})


@dataclass
class Plan:
    """План записи страницы: клетки и отказы раскладки."""

    page: str
    cells: list[PlannedCell] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _cell_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _cell_hour(value: Any) -> int | None:
    """Подпись часа в колонке A: `08:00` (а также `8:00` и `08.00`)."""
    text = str(value or "").strip().replace(".", ":")
    if ":" not in text:
        return None
    head = text.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _page_dates(header: list) -> tuple[dict[date, int], list[str]]:
    """Колонки дат из строки 1: дата → номер колонки (1-based) и найденные дубли."""
    dates: dict[date, int] = {}
    duplicates: list[str] = []
    for column, value in enumerate(header, start=1):
        day = _cell_day(value)
        if day is None:
            continue
        if day in dates:
            duplicates.append(day.isoformat())
            continue
        dates[day] = column
    return dates, duplicates


def _page_hours(values: list[list]) -> dict[int, int]:
    """Строки часов из колонки A: час → номер строки (1-based)."""
    hours: dict[int, int] = {}
    for row, line in enumerate(values, start=1):
        if not line:
            continue
        hour = _cell_hour(line[0])
        if hour is not None and hour not in hours:
            hours[hour] = row
    return hours


def plan_probes(values: list[list], page: str, probes: list[Probe]) -> Plan:
    """Адреса проб по раскладке `hour_matrix`; отсутствующая дата/час — расхождение.

    Ничего не создаёт и не додумывает: дата или час вне страницы — `plan_problems`
    (186 §4.3 шаг 7), потому что «поставить строку самому» значит сдвинуть человека.
    """
    plan = Plan(page=page)
    if not values:
        plan.problems.append("page_empty")
        return plan
    dates, duplicates = _page_dates(values[0])
    plan.problems.extend(f"day_ambiguous: {day}" for day in duplicates)
    hours = _page_hours(values)
    for probe in probes:
        if probe.status not in STATUSES:
            plan.problems.append(f"status_unknown: {probe.status}")
            continue
        column = dates.get(probe.day)
        if column is None:
            plan.problems.append(f"day_not_in_page: {probe.day.isoformat()}")
            continue
        if int(probe.hour) not in HOURS:
            plan.problems.append(f"hour_not_in_page: {int(probe.hour):02d}")
            continue
        row = hours.get(int(probe.hour))
        if row is None:
            plan.problems.append(f"hour_not_in_page: {int(probe.hour):02d}")
            continue
        status = STATUSES[probe.status]
        plan.cells.append(PlannedCell(
            a1=f"{sheet_binding.column_name(column)}{row}",
            text=str(probe.text or status["text"]),
            color=dict(status["color"]),
            entity_id=f"{page}:{probe.day.isoformat()}:{int(probe.hour):02d}",
            cell_key=f"{probe.day.isoformat()}T{int(probe.hour):02d}",
            sheet_row=row, sheet_col=column))
    return plan


def _page_values(session: sheet_binding.SheetSession) -> list[list]:
    """Значения страницы: строки 1…40, как в контракте чтения 186 §3."""
    try:
        return session.service.spreadsheets().values().get(
            spreadsheetId=session.spreadsheet_id, range=f"{session.a1}{PAGE_RANGE}",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as exc:
        raise sheet_binding._google_error(exc, "sheet_unavailable") from None


def _after_write_time(session: sheet_binding.SheetSession, drive: Any = None):
    """Время файла после записи: своя правка двигает `modifiedTime`, и его надо учесть.

    Без этого следующая проба в тот же файл упрётся в `sheet_changed_since_read`.
    Если Drive не отдал новое время (кэш, нет прав), берём момент записи: он заведомо
    позже прочитанного ранее, а чужая правка после нас будет ещё позже.
    """
    stamp = sheet_binding.file_modified_time(session.spreadsheet_id, drive)
    if stamp is None or (session.modified_time and stamp <= session.modified_time):
        return datetime.now(UTC)
    return stamp


def write_probes(conn, *, month: str, nick: str, probes: list[Probe],
                 actor_id: int | None = None, apply: bool = True,
                 page: str | None = None, service: Any = None,
                 drive: Any = None) -> dict:
    """Записать пробы страницы по протоколу 186 §4.3. Возвращает отчёт, а не «ок».

    Шаги протокола выполняет `sheet_binding.open_sheet` (ключ реестра, режим,
    привязка, страница по имени, сумма шапки, свежесть). Здесь остаются раскладка,
    дедуп по `row_map`, запись текста и цвета, перечитывание адресов и отметки.
    """
    label = month_label(month)
    page_name = (page or "").strip() or page_title(core.SCHEDULE_TEST_SHEET_PATTERN,
                                                   label, nick)
    if not page_name:
        raise ApiError(409, "sheet_ambiguous: страница тестового листа не задана",
                       code="sheet_ambiguous")

    session = sheet_binding.open_sheet(KEY, conn=conn, write=True, service=service,
                                       drive=drive, title=page_name)
    values = _page_values(session)
    plan = plan_probes(values, session.sheet_title, probes)
    if plan.problems:
        raise ApiError(409, f"plan_problems: {', '.join(plan.problems)}",
                       code="plan_problems")

    pending: list[PlannedCell] = []
    skipped: list[PlannedCell] = []
    for cell in plan.cells:
        found = sheet_binding.row_map_find(conn, KEY, ENTITY_TYPE, cell.entity_id,
                                           cell.cell_key)
        if found is not None and found["content_hash"] == cell.content_hash:
            skipped.append(cell)
            continue
        pending.append(cell)

    report = {"ok": True, "sheet": session.sheet_title, "applied": False,
              "verified": True, "skipped": len(skipped),
              "cells": [{"cell": cell.a1, "text": cell.text, "color": cell.color}
                        for cell in plan.cells]}
    if not apply or not pending:
        return report

    text_cells = [(cell.a1, cell.text) for cell in pending]
    color_cells = [(cell.a1, cell.color) for cell in pending]
    sheet_binding.write_cells(session, text_cells, value_input_option="RAW")
    sheet_binding.write_colors(session, color_cells)

    mismatched = sheet_binding.verify_cells(session, text_cells)
    mismatched.extend(sheet_binding.verify_colors(session, color_cells))
    if mismatched:
        raise ApiError(502,
                       f"sheet_verify_failed: лист не подтвердил {len(mismatched)} клеток",
                       code="sheet_verify_failed")

    for cell in pending:
        sheet_binding.row_map_save(conn, KEY, ENTITY_TYPE, cell.entity_id,
                                   sheet_row=cell.sheet_row, col_from=cell.sheet_col,
                                   col_to=cell.sheet_col, cell_key=cell.cell_key,
                                   content_hash=cell.content_hash)
    sheet_binding.remember_header(conn, session)
    sheet_binding.touch_after_write(conn, session, _after_write_time(session, drive))
    core.audit(conn, "human", actor_id, "availability.test_sheet", "sheet_probe",
               session.sheet_title,
               after={"written": len(pending), "skipped": len(skipped),
                      "cells": [cell.a1 for cell in pending]})
    report.update({"applied": True, "written": len(pending)})
    return report


def pages(service: Any = None) -> tuple[list[dict], str]:
    """Страницы тестового файла: (properties, id). Только чтение, для отчёта/скрипта."""
    spreadsheet_id = google_targets.resolve(KEY)
    client = service if service is not None else core._sheets()
    try:
        answer = client.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                           fields=sheet_binding.SHEET_FIELDS).execute()
    except GOOGLE_API_ERRORS as exc:
        raise sheet_binding._google_error(exc, "sheet_unavailable") from None
    return list(answer.get("sheets") or []), spreadsheet_id

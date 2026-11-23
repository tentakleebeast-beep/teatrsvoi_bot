"""Репетиция цветов тестового листа по живому «Расписание студийцы» (186 §13 п.3, AC-10).

Зачем отдельный модуль. Скрипт `scripts/fill_test_schedule.py` держал людей и ники
литералами (`PEOPLE`/`NICKS`), брал источник из файла `.hermes/desktop-attachments/…xlsx`,
которого в рабочем дереве нет, и создавал страницы сам. Живой прогон 186 §13 показал
цену: единственная команда просмотра (`--list`) не существовала, а пять настоящих
страниц тестового листа (`11.2026 · Даина`, `… · Гагарина`, …) не совпадали с
синтетическими «Дарья Смирнова» и другими, поэтому режим чтения отвечал «нет (0)»
на живом файле.

Правила здесь — те же, что у сервиса, и по одной причине: расхождение скрипта и
маршрута уже один раз приводило к записи не туда.

* **Кто человек** решает `schedule_sheet.resolve_column` по шапке живого листа, а не
  список в скрипте: ник страницы (`Даина`) — это подпись колонки в листе.
* **Что в клетке** решает `month_grid.sheet_cell`: `1`/`0` — раскрашенная сетка,
  остальной текст — карточка дня; неразобранный текст не угадывается.
* **Источник** — живой лист `SCHEDULE_HUMAN_*` через `live_sheet_values`, только чтение.

Модуль чистый там, где это возможно: `page_reports` и `describe_pages` получают значения
листа и список страниц, поэтому обе ветки (страницы есть/нет, человек найден/нет)
проверяются без сети и без БД.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app import core, google_targets, month_grid, schedule_sheet, schedule_test_sheet
from app.external import GOOGLE_API_ERRORS
from app.schedule_grid import hours_from_text

#: Разделитель в имени страницы: `11.2026 · Ник`.
PAGE_SEP = "·"

#: Месяц страницы: `11.2026`.
MONTH_RE = re.compile(r"^(\d{1,2})\.(\d{4})$")

#: Клетки раскрашенной сетки: `1` — занят, `0` — свободен (docs/development/22).
GRID_CELLS = {"1": "busy", "0": "free"}

#: Отказы репетиции словами. Код — для скрипта и теста, текст — человеку.
PROBLEMS: dict[str, str] = {
    "header_not_found": "шапка листа не найдена",
    "person_not_found": "нет колонки человека в листе",
    "person_ambiguous": "подпись колонки неоднозначна",
    "not_month_page": "не страница месяца",
    "no_cells_in_month": "в листе нет дней этого месяца",
    "no_probes": "в клетках месяца нет разобранных проб",
}


@dataclass(frozen=True, slots=True)
class Page:
    """Страница тестового листа: её имя, месяц и ник человека."""

    title: str
    month: str          # `MM.YYYY`, как в имени страницы
    nick: str           # подпись человека, по ней ищется колонка в живом листе


@dataclass
class PageReport:
    """Что репетиция поняла про одну страницу: человек, пробы и отказы."""

    page: Page
    column: int | None = None
    person: str = ""
    probes: list[schedule_test_sheet.Probe] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def parse_page(title: str) -> Page | None:
    """`11.2026 · Даина` → страница. «студийцы (копия)» и прочее — не страница месяца."""
    text = str(title or "").strip()
    left, sep, right = text.partition(PAGE_SEP)
    if not sep:
        return None
    month, nick = left.strip(), right.strip()
    match = MONTH_RE.match(month)
    if not match or not nick:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 12:
        return None
    return Page(title=text, month=month, nick=nick)


def pages(titles: list[str], month: str | None = None) -> list[Page]:
    """Страницы месяца из всех имён листов файла. `month` — `2026-11` или `11.2026`."""
    wanted = schedule_test_sheet.month_label(month) if month else ""
    out: list[Page] = []
    for title in titles:
        page = parse_page(title)
        if page is None:
            continue
        if wanted and page.month != wanted:
            continue
        out.append(page)
    return out


def live_sheet_values(service: Any = None) -> tuple[list[list] | None, str]:
    """Значения общего листа «Расписание студийцы» целиком: (строки, причина отказа).

    Источник — отдельная пара настроек `SCHEDULE_HUMAN_*`: `SCHEDULE_SPREADSHEET_ID`
    смотрит на плоский журнал «Занятость», и загрузка на нём читала бы пустой первый
    лист (docs/development/149 §2.1). Диапазон тот же `A1:AZ3000`, что у остальных
    читателей листа: он растёт вниз, и фиксированные 400 строк из замера стали бы
    обрезкой. Функция одна на маршрут и на скрипт репетиции: второй копии чтения быть
    не должно, иначе скрипт покажет не то, что видит сервис.
    """
    if not core.SCHEDULE_HUMAN_SPREADSHEET_ID:
        return None, "no_spreadsheet"
    try:
        client = service if service is not None else core._sheets_read()
        values = client.spreadsheets().values().get(
            spreadsheetId=google_targets.read_id("schedule.human"),
            range=f"'{core.SCHEDULE_HUMAN_SHEET_TITLE}'!A1:AZ3000",
            majorDimension="ROWS").execute().get("values", [])
    except GOOGLE_API_ERRORS as exc:
        core.log.warning("общий лист «Расписание студийцы» недоступен: %s", exc)
        return None, "sheet_unavailable"
    return values, ""


def person_column(values: list[list], nick: str) -> tuple[int | None, str]:
    """Колонка человека в живом листе по нику страницы: (колонка, код отказа)."""
    columns, names_row = schedule_sheet.resolve_column(values, nick)
    if names_row is None:
        return None, "header_not_found"
    if not columns:
        return None, "person_not_found"
    if len(columns) > 1:
        return None, "person_ambiguous"
    return columns[0], ""


def person_name(values: list[list], column: int) -> str:
    """Подпись колонки в шапке листа — как человек записан на самом деле."""
    names_row = schedule_sheet.find_names_row(values)
    if names_row is None or not 1 <= names_row <= len(values):
        return ""
    row = values[names_row - 1]
    return str(row[column - 1] if len(row) >= column else "" or "").strip()


def month_days(values: list[list], month: str) -> dict[date, int]:
    """Строки живого листа за месяц: дата → номер строки (1-based)."""
    return {day: row for day, row in schedule_sheet.find_day_rows(values).items()
            if day.strftime("%m.%Y") == month}


def cell_probes(raw: Any, day: date) -> tuple[list[schedule_test_sheet.Probe], str | None]:
    """Клетка живого листа за день → пробы (день, час, статус) для тестовой страницы.

    `1`/`0` раскрашенной сетки не проходят через разбор текста: `hours_from_text("1")`
    честно называет их «не уверен», а для сетки это «весь день занят/свободен»
    (`docs/development/22`, `month_grid.sheet_cell`). Неразобранный текст не угадывается:
    он не даёт проб, а возвращает код пропуска для счёта в отчёте.
    """
    state, text = month_grid.sheet_cell(raw)
    if state == "empty":
        return [], "empty"
    if state == "unparsed":
        return [], "unparsed"
    statuses = ({hour: state for hour in schedule_test_sheet.HOURS}
                if text in GRID_CELLS else hours_from_text(text))
    probes = [schedule_test_sheet.Probe(day=day, hour=hour, status=status)
              for hour, status in sorted(statuses.items()) if status]
    return probes, None if probes else "unparsed"


def page_report(values: list[list], page: Page) -> PageReport:
    """Страница → человек, пробы за её месяц и отказы. Ничего не пишет и не создаёт."""
    report = PageReport(page=page)
    column, problem = person_column(values, page.nick)
    if column is None or problem:
        report.problems.append(problem or "person_not_found")
        return report
    report.column = column
    report.person = person_name(values, column)

    days = month_days(values, page.month)
    if not days:
        report.problems.append("no_cells_in_month")
        return report

    counts = {"busy": 0, "free": 0, "uncertain": 0, "empty": 0, "unparsed": 0}
    for day, row in sorted(days.items()):
        line = values[row - 1] if row - 1 < len(values) else []
        raw = line[column - 1] if len(line) >= column else ""
        probes, skip = cell_probes(raw, day)
        if skip:
            counts[skip] += 1
        for probe in probes:
            report.probes.append(probe)
            counts[probe.status] += 1
    report.counts = counts
    if not report.probes:
        # Дни месяца в листе есть, а клетки человека пусты или не разобраны:
        # это не «нет дней месяца», и говорить так человеку нельзя.
        report.problems.append("no_probes")
    return report


def page_reports(values: list[list], titles: list[str], month: str | None = None) -> list[PageReport]:
    """Отчёты по всем страницам месяца: единственный вход для просмотра и записи."""
    return [page_report(values, page) for page in pages(titles, month)]


__all__ = [
    "GRID_CELLS",
    "PROBLEMS",
    "Page",
    "PageReport",
    "cell_probes",
    "live_sheet_values",
    "month_days",
    "page_report",
    "page_reports",
    "pages",
    "parse_page",
    "person_column",
    "person_name",
]

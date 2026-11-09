"""
Загрузка своей колонки из общего листа «Расписание студийцы» (Э5, FR-C9, docs/development/149).

Зачем отдельный модуль: у записи недели **в** лист уже есть разбор адресов
(`schedule_sheet.py`), но обратное действие решает другие вопросы — где человек в листе,
чем клетка отличается от базы и что именно писать. Смешивать это с записью нельзя:
у загрузки правило «источник только читаем», у записи — «пишем канон и снимок».

Модуль чистый: на вход значения листа и карточки базы, на выход дни, состояния и план
записи. Ни HTTP, ни Google, ни БД — поэтому все состояния дня проверяются тестами без сети.

Три правила, которые нельзя разнести по разным местам:

* **состояние дня** (`day_state`) считается здесь и только здесь: `new`, `differs`, `same`,
  `sheet_empty`, `unparsed`. Экран их не пересчитывает, загрузка не пересчитывает тоже;
* **сравнение по смыслу** (`same_day` из `schedule_sheet`), а не по буквам: «занят до 19:00»
  и «свободен с 19:00» — одна занятость. Клетки раскрашенной сетки `1`/`0` сравниваются
  иначе: истина только для карточки «весь день» того же состояния (149 §4.3);
* **неразобранный текст не грузится** (`unparsed`), а «?» грузится как `uncertain`:
  «пока не понятно» — сказанное человеком состояние, а не мусор (149 D6).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from app import month_grid
from app.availability_card import DayCard, parse, to_text
from app.schedule_sheet import column_letter, find_day_rows, resolve_column, same_day

#: Имя таблицы для человека и для аудита — одно, чтобы в журнале не было двух названий.
SOURCE_TITLE = "Расписание студийцы"

#: Клетки раскрашенной сетки: `1` — занят, `0` — свободен (docs/development/22).
GRID_CELLS = {"1": "busy", "0": "free"}

#: Состояния дня, которые нельзя загрузить, но и молчать о них нельзя.
STATES = ("new", "differs", "same", "sheet_empty", "unparsed")

#: Что человек увидит при отказе. Один словарь на предпросмотр и загрузку.
DETAILS = {
    "person_not_found": "В общем листе нет колонки с вашим именем."
                        " Попросите организатора её добавить.",
    "person_ambiguous": "В листе две колонки с вашим именем: {columns}."
                        " Попросите организатора оставить одну.",
    "sheet_unavailable": "Общий лист сейчас недоступен. Попробуйте позже.",
    "no_spreadsheet": "Общий лист сейчас недоступен. Попробуйте позже.",
    "nothing_to_write": "В листе и в приложении одно и то же — загружать нечего.",
}


def detail(code: str, columns: Iterable[int] = ()) -> str:
    """Текст отказа человеку: единственное место, где эти фразы написаны."""
    letters = ", ".join(column_letter(column) for column in columns)
    return DETAILS[code].format(columns=letters or "—")


def grid_cell(value: str) -> bool:
    """Клетка раскрашенной сетки: `1` или `0`."""
    return " ".join(str(value or "").split()) in GRID_CELLS


def _grid_same(db_text: str, sheet_state: str) -> bool:
    """`1`/`0` против базы: совпало, только если в базе «весь день» того же состояния.

    Сетка не знает границ, поэтому «занят с 16 до 20» против `1` — расхождение:
    стереть в нём интервал значило бы соврать (149 §4.3).
    """
    card = parse(db_text)
    return card is not None and card.is_whole_day and card.status == sheet_state


def cell_card(sheet_text: str) -> DayCard | None:
    """Текст клетки листа → карточка для записи в базу.

    `parse` не знает клеток раскрашенной сетки: `1`/`0` он читает как «пока не понятно»,
    хотя предпросмотр уже назвал их `busy`/`free`. Запись обязана получить то же
    состояние, что человек видел в листе (149 §4.3), иначе загрузка `1` даёт
    `uncertain` вместо «занят весь день». Поэтому `1`/`0` переводятся в карточку
    «весь день», а остальной текст идёт обычным разбором.
    """
    key = " ".join(str(sheet_text or "").split())
    if key in GRID_CELLS:
        return DayCard(status=GRID_CELLS[key])
    return parse(key)


def day_state(sheet_state: str, sheet_text: str, db_text: str | None) -> str:
    """Состояние дня: единственное место, где решается `new`/`differs`/`same`/...

    `None` в `db_text` значит «в базе дня нет», пустая строка — «отметка есть, текста нет»
    (так не бывает, но пусть правило будет одно). Отсутствие обеих сторон — `absent`:
    такой день в предпросмотр не попадает.
    """
    if sheet_state == "unparsed":
        return "unparsed"
    if sheet_state == "empty":
        return "sheet_empty" if db_text else "absent"
    if not db_text:
        return "new"
    if grid_cell(sheet_text):
        return "same" if _grid_same(db_text, sheet_state) else "differs"
    return "same" if same_day(db_text, sheet_text) else "differs"


def build_preview(
    values: list[list],
    display_name: str,
    aliases: Iterable[str],
    days: list[date],
    db: Mapping[date, str | None],
    *,
    female: bool = False,
) -> dict:
    """Предпросмотр недели: где колонка, что в листе, что в базе и чем они расходятся.

    `db` — канонический текст карточки дня из базы (`None` — дня нет). Ничего не пишет:
    это ответ на открытие листа, а не действие (149 D4).
    """
    columns, _names_row = resolve_column(values, display_name, aliases)
    if not columns:
        return {"ok": False, "error": "person_not_found", "columns": []}
    if len(columns) > 1:
        return {"ok": False, "error": "person_ambiguous", "columns": columns}

    column = columns[0]
    rows = find_day_rows(values)
    counts: dict[str, int] = {state: 0 for state in STATES}
    out: list[dict] = []
    for day in days:
        row = rows.get(day)
        line = values[row - 1] if row is not None and row - 1 < len(values) else []
        raw = line[column - 1] if len(line) >= column else ""
        sheet_state, sheet_text = month_grid.sheet_cell(raw)
        card_text = db.get(day)
        db_text = card_text or None
        state = day_state(sheet_state, sheet_text, db_text)
        if state == "absent":
            continue
        counts[state] += 1
        card = parse(db_text) if db_text else None
        out.append({
            "day": day.isoformat(),
            "sheet_text": sheet_text,
            "sheet_state": sheet_state,
            "db_text": to_text(card, female=female) if card is not None else None,
            "db_state": card.status if card is not None else "empty",
            "state": state,
        })
    return {"ok": True, "column": column, "days": out, "counts": counts}


def plan_load(days: list[dict], requested: list[date]) -> dict:
    """Что писать по выбранным человеком дням: записи, пропуски и признак «писать нечего».

    Из клиента приходят только даты (149 §4.4): состояние дня считается заново по листу,
    иначе экран мог бы прислать что угодно. Дни, которых нет в предпросмотре, — `empty`.
    """
    by_day = {item["day"]: item for item in days}
    write: list[dict] = []
    skipped: list[dict] = []
    for day in requested:
        item = by_day.get(day.isoformat())
        if item is None:
            skipped.append({"day": day.isoformat(), "reason": "empty"})
            continue
        state = item["state"]
        if state in ("new", "differs"):
            write.append({"day": day, "state": state, "text": item["sheet_text"],
                          "sheet_state": item["sheet_state"]})
        elif state == "sheet_empty":
            write.append({"day": day, "state": state, "text": "", "sheet_state": "empty"})
        elif state == "same":
            skipped.append({"day": day.isoformat(), "reason": "same"})
        else:
            skipped.append({"day": day.isoformat(), "reason": "unparsed"})
    return {"ok": bool(write), "write": write, "skipped": skipped}


__all__ = [
    "DETAILS",
    "GRID_CELLS",
    "SOURCE_TITLE",
    "STATES",
    "build_preview",
    "cell_card",
    "day_state",
    "detail",
    "grid_cell",
    "plan_load",
]

"""Сетка месяца «люди × дни» — только чтение (FR-H9, З9).

Зачем отдельный модуль: экран З9 показывает месяц не из базы, а из
Google-таблицы «Расписание студийцы» — она источник истины для занятости
(docs/development/22 §1, docs/reports/124 §3). База нужна как вторая сторона:
там, где таблица и база расходятся, экран показывает **расхождение**, а не
правит его (П-3). Раньше эта же сетка умела писать отметки — правка ушла в
личный лист дня (З1), месяц остался только для глаз.

Модуль чистый: на вход значения листа (список строк из Google Sheets) и
состояния из базы, на выход то, что рисует экран. Ни HTTP, ни БД, ни Google —
поэтому расхождения, пустой лист и «человека нет в таблице» проверяются
тестами без сети.

Словарь состояний один на весь экран: `WORDS`. Экран не придумывает свои слова,
иначе «занят» в клетке и «занят» в списке расхождений разъедутся.
"""

from __future__ import annotations

from datetime import date

from app.availability_card import parse
from app.schedule_grid import TEXT_RULES
from app.schedule_sheet import find_day_rows, find_names_row, find_person_columns

#: Состояние дня словами — единственный словарь для клетки, легенды и расхождений.
WORDS: dict[str, str] = {
    "free": "свободен",
    "busy": "занят",
    "uncertain": "не уверен",
    "empty": "не отмечено",
    "unparsed": "не разобрано",
}

#: Откуда взято состояние клетки.
SHEET = "sheet"
BASE = "base"

#: Почему у человека нет колонки в таблице.
MISSING = "нет в таблице"
AMBIGUOUS = "имя в таблице неоднозначно"

_UNCERTAIN_WORDS = TEXT_RULES[0][1]


def _uncertain_named(text: str) -> bool:
    """Человек действительно сказал «не уверен», а не написал что-то неразборчивое."""
    lowered = text.lower()
    return any(word in lowered for word in _UNCERTAIN_WORDS) or "?" in lowered


def sheet_cell(value) -> tuple[str, str]:
    """Клетка листа → (состояние, текст как он записан). Пустая клетка → «не отмечено».

    «1» и «0» — как в раскрашенной сетке (docs/development/22, `parse_vertical_grid`):
    единица значит «занят», ноль — «свободен». Текст, который не назвал ни
    состояния, ни границы времени, не угадывается: он честно называется
    «не разобрано» и попадает в список расхождений.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return "empty", ""
    if text in ("1", "0"):
        return ("busy" if text == "1" else "free"), text
    card = parse(text)
    if card is None:
        return "empty", ""
    if card.status == "uncertain" and not _uncertain_named(text):
        return "unparsed", text
    return card.status, text


def _cell(state: str, text: str, base: str) -> dict:
    """Одна клетка: что показать, что в базе и расходятся ли они."""
    return {
        "state": state,
        "word": WORDS.get(state, state),
        "text": text,
        "base": base,
        "base_word": WORDS.get(base, base),
        "diff": state != base,
        "source": BASE if state == "empty" and not text else SHEET,
    }


def _base_cell(base: str) -> dict:
    """Клетка только из базы: лист по ней ничего не сказал (нет колонки, дня, связи).

    Показывать «не отмечено» вместо известного из базы было бы враньём, а считать
    это расхождением — шумом на весь месяц. Поэтому клетка берёт состояние базы,
    помечает источник и в список расхождений не попадает.
    """
    cell = _cell(base, "", base)
    cell["source"] = BASE
    cell["diff"] = False
    return cell


def _diff_line(person: str, day: str, state: str, text: str, base: str) -> dict:
    """Строка списка расхождений — словами, без символов и цветов."""
    where = "в таблице" if state != "unparsed" else "в таблице записано"
    left = text if state == "unparsed" else WORDS.get(state, state)
    return {"day": day, "person": person, "sheet": left, "base": WORDS.get(base, base),
            "text": f"{person}: {where} «{left}», в базе «{WORDS.get(base, base)}»"}


def build(
    *,
    month: str,
    days: list[date],
    people: list[dict],
    marks: dict,
    sheet_values: list[list] | None,
    sheet_reason: str = "",
) -> dict:
    """Месяц «люди × дни»: состояние каждой клетки, расхождения и честный источник.

    `sheet_values` — строки листа целиком (A1:AZ3000), `None` — лист недоступен:
    тогда показываем базу и говорим об этом прямо. `marks` — состояния из базы
    (`participant_id → {день: {state}}`), `people` — те, кто в базе не в архиве.
    """
    day_iso = [day.isoformat() for day in days]
    values = sheet_values or []
    day_rows = find_day_rows(values)
    names_row = find_names_row(values)
    missing_days = [day for day in day_iso if day not in {d.isoformat() for d in day_rows}]

    cells: dict[str, dict[str, dict]] = {}
    rows: list[dict] = []
    diffs: list[dict] = []

    for person in people:
        pid = person["id"]
        name = person["name"]

        columns = (find_person_columns(values, names_row, name)
                   if names_row is not None else [])
        if sheet_values is None:
            problem = "лист недоступен"
        elif names_row is None:
            problem = "шапка таблицы не найдена"
        elif not columns:
            problem = MISSING
        elif len(columns) > 1:
            problem = AMBIGUOUS
        else:
            problem = ""

        cells[str(pid)] = {}
        for day in days:
            iso = day.isoformat()
            base = marks.get(pid, {}).get(iso, {}).get("state") or "empty"
            if problem or iso in missing_days:
                cells[str(pid)][iso] = _base_cell(base)
                continue
            row_index = day_rows[day]
            line = values[row_index - 1] if row_index - 1 < len(values) else []
            column = columns[0]
            raw = line[column - 1] if len(line) >= column else ""
            state, text = sheet_cell(raw)
            cell = _cell(state, text, base)
            cells[str(pid)][iso] = cell
            if cell["diff"]:
                diffs.append(_diff_line(name, iso, state, text, base))
                cell["diff_text"] = diffs[-1]["text"]

        rows.append({"id": pid, "name": name, "self": bool(person.get("self")),
                     "sheet": problem, "in_sheet": not problem})

    available = sheet_values is not None
    month_in_sheet = available and bool(day_rows) and len(missing_days) < len(day_iso)
    sheet = {"available": available, "reason": sheet_reason,
             "days_missing": missing_days, "month_in_sheet": month_in_sheet}
    note = ""
    if not available:
        note = "Лист недоступен — показана база. Отметки в таблице не показаны."
    elif not month_in_sheet:
        note = "Этого месяца в таблице нет — показана база."
    elif missing_days:
        note = "В таблице нет строк для части дней месяца — по ним показана база."

    return {
        "ok": True,
        "month": month,
        "days": day_iso,
        "weekdays": [day.weekday() for day in days],
        "words": dict(WORDS),
        "people": rows,
        "cells": cells,
        "diffs": diffs,
        "sheet": sheet,
        "note": note,
    }


__all__ = ["AMBIGUOUS", "BASE", "MISSING", "SHEET", "WORDS", "build", "sheet_cell"]

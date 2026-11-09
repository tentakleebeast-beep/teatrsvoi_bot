"""
Предпросмотр и план загрузки своей колонки из общего листа (Э5, FR-C9).

Состояния дня — единственное, что отличает «включено по умолчанию» от «человек должен
решить сам»: `new` грузится, `differs` и `sheet_empty` выключены, `unparsed` не грузится
вовсе (docs/development/149 §4.3, D4–D6). Поэтому проверяем их на мини-листе и мини-базе:
ни Google, ни БД здесь нет — модуль чистой логики.

Обоснование: FR-C9, docs/development/149 §4.2–4.4, §9.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from app.sheet_load import build_preview, day_state, detail, grid_cell, plan_load

MONDAY = date(2026, 11, 9)
WEEK = [MONDAY + timedelta(days=offset) for offset in range(7)]
NAME = "Виктор Мамлютов"


def sheet(cells: dict[date, str], name: str = NAME) -> list[list]:
    """Мини-лист как живой: шапка, блок месяца, даты в колонке B, отметки в колонке E."""
    values: list[list] = [["", "", "Решение", "Дарья Смирнова", name]]
    values.append(["", "ноябрь", "", "", ""])
    for day in sorted(cells):
        row: list = [day.strftime("%d.%m.%y"), day, "", "", cells[day]]
        values.append(row)
    return values


def preview(cells: dict[date, str], db: Mapping[date, str | None] | None = None) -> dict:
    return build_preview(sheet(cells), NAME, [], WEEK, db or {})


def by_day(result: dict) -> dict[str, dict]:
    return {item["day"]: item for item in result["days"]}


def test_fr_c9_шесть_состояний_дня_и_их_счёт() -> None:
    """FR-C9: new/differs/same/sheet_empty/unparsed считаются, пустое с обеих сторон — нет."""
    cells = {
        WEEK[0]: "свободен",            # new: в базе пусто
        WEEK[1]: "свободен",            # differs: в базе занят
        WEEK[2]: "занят до 18:00",      # same: в базе та же занятость
        WEEK[3]: "",                    # sheet_empty: в базе есть
        WEEK[4]: "как получится",       # unparsed
        WEEK[5]: "",                    # пусто и там, и там — не показываем
    }
    db = {WEEK[1]: "занят", WEEK[2]: "занят до 18:00", WEEK[3]: "занят весь день"}
    result = preview(cells, db)

    assert result["ok"] is True
    assert result["column"] == 5
    days = by_day(result)
    assert days[WEEK[0].isoformat()]["state"] == "new"
    assert days[WEEK[1].isoformat()]["state"] == "differs"
    assert days[WEEK[2].isoformat()]["state"] == "same"
    assert days[WEEK[3].isoformat()]["state"] == "sheet_empty"
    assert days[WEEK[4].isoformat()]["state"] == "unparsed"
    assert WEEK[5].isoformat() not in days and WEEK[6].isoformat() not in days
    assert result["counts"] == {"new": 1, "differs": 1, "same": 1,
                                "sheet_empty": 1, "unparsed": 1}


def test_fr_c9_день_отдаёт_обе_стороны_и_слова() -> None:
    """FR-C9: экран показывает, что в листе и что в базе, а не только «расходится»."""
    cells = {WEEK[0]: "свободен"}
    db = {WEEK[0]: "занят с 18:00"}
    item = by_day(preview(cells, db))[WEEK[0].isoformat()]
    assert item == {"day": WEEK[0].isoformat(), "sheet_text": "свободен",
                    "sheet_state": "free", "db_text": "занят с 18:00",
                    "db_state": "busy", "state": "differs"}


def test_fr_c9_сравнение_по_смыслу_а_не_по_буквам() -> None:
    """FR-C9: «занят до 19:00» и «свободен с 19:00» — одна занятость (149 D3)."""
    cells = {WEEK[0]: "свободен с 19:00"}
    db = {WEEK[0]: "занят до 19:00"}
    assert by_day(preview(cells, db))[WEEK[0].isoformat()]["state"] == "same"


def test_fr_c9_клетка_сетки_совпадает_только_с_целым_днём() -> None:
    """FR-C9: `1`/`0` — раскрашенная сетка без границ: интервал против неё расхождение."""
    cells = {WEEK[0]: "1", WEEK[1]: "0", WEEK[2]: "1"}
    db = {WEEK[0]: "занят весь день", WEEK[1]: "свободен", WEEK[2]: "занят с 16 до 20"}
    days = by_day(preview(cells, db))
    assert days[WEEK[0].isoformat()]["state"] == "same"
    assert days[WEEK[1].isoformat()]["state"] == "same"
    assert days[WEEK[2].isoformat()]["state"] == "differs"
    assert grid_cell("1") is True and grid_cell("свободен") is False


def test_fr_c9_вопрос_грузится_как_неуверенность() -> None:
    """FR-C9: «?» — сказанное человеком «пока не понятно», а не мусор (149 D6)."""
    cells = {WEEK[0]: "?"}
    item = by_day(preview(cells, {WEEK[0]: "пока не понятно"}))[WEEK[0].isoformat()]
    assert (item["sheet_state"], item["state"]) == ("uncertain", "same")
    fresh = by_day(preview(cells))[WEEK[0].isoformat()]
    assert (fresh["sheet_state"], fresh["state"]) == ("uncertain", "new")


def test_fr_c9_неразобранное_и_пустое_различаются() -> None:
    """FR-C9: `unparsed` нельзя загрузить, `empty` — «в листе пусто» (149 D5, D6)."""
    assert day_state("unparsed", "как получится", None) == "unparsed"
    assert day_state("empty", "", "занят") == "sheet_empty"
    assert day_state("empty", "", None) == "absent"
    assert day_state("free", "свободен", None) == "new"


def test_fr_c9_план_берёт_только_выбранные_дни() -> None:
    """FR-C9: выбор человека — закон: непереданные дни не меняются, лишнее уходит в skipped."""
    cells = {WEEK[0]: "свободен", WEEK[1]: "свободен", WEEK[2]: "занят", WEEK[3]: "как получится"}
    db = {WEEK[2]: "занят", WEEK[3]: "занят"}
    days = preview(cells, db)["days"]
    plan = plan_load(days, [WEEK[0], WEEK[2], WEEK[3], WEEK[4]])
    assert [(item["day"], item["state"], item["text"]) for item in plan["write"]] == [
        (WEEK[0], "new", "свободен")]
    assert plan["skipped"] == [
        {"day": WEEK[2].isoformat(), "reason": "same"},
        {"day": WEEK[3].isoformat(), "reason": "unparsed"},
        {"day": WEEK[4].isoformat(), "reason": "empty"},
    ]
    assert plan["ok"] is True


def test_fr_c9_пустой_день_в_листе_стирается_только_выбранным() -> None:
    """FR-C9: пустая клетка не стирает отметку молча — только если человек её включил."""
    cells = {WEEK[0]: ""}
    db = {WEEK[0]: "занят весь день"}
    days = preview(cells, db)["days"]
    assert plan_load(days, [WEEK[0]])["write"] == [
        {"day": WEEK[0], "state": "sheet_empty", "text": "", "sheet_state": "empty"}]


def test_fr_c9_нечего_грузить() -> None:
    """FR-C9: всё совпало — `nothing_to_write`, а не «загружено 0 дней» (149 §4.4)."""
    cells = {WEEK[0]: "свободен"}
    db = {WEEK[0]: "свободен"}
    plan = plan_load(preview(cells, db)["days"], [WEEK[0]])
    assert plan == {"ok": False, "write": [],
                    "skipped": [{"day": WEEK[0].isoformat(), "reason": "same"}]}


def test_fr_c9_день_вне_листа_не_выдумывается() -> None:
    """FR-C9: недели нет в листе — все дни пустые, а не «свободен по умолчанию»."""
    values = [["", "", "Решение", NAME], ["", datetime(2026, 10, 1), "", "занят"]]
    result = build_preview(values, NAME, [], WEEK, {})
    assert result["ok"] is True and result["days"] == []


def test_fr_c9_тексты_отказов_написаны_один_раз() -> None:
    """FR-C9: экран не придумывает свои фразы — буквы колонок подставляет сервер."""
    assert detail("person_not_found").startswith("В общем листе нет колонки")
    assert "F, H" in detail("person_ambiguous", [6, 8])
    assert detail("no_spreadsheet") == detail("sheet_unavailable")

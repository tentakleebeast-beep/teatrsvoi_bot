"""Сетка месяца «люди × дни»: лист — источник, база — вторая сторона (FR-H9, З9).

Обоснование: FR-H9 (сводка «люди × дни» — только чтение, только организатору),
план 126 Ш-04, docs/reports/124 §3 (З9) и docs/development/48 §3. Дефект, который
сторожат тесты: месяц читался из базы, правился через сетку и не показывал, что
Google-таблица и база расходятся. Здесь проверяется чистый сборщик ответа:
слова состояний, расхождения, недоступный лист и человек без колонки.
"""

from __future__ import annotations

import copy
import datetime as dt

from app import month_grid

DAYS = [dt.date(2026, 11, 9) + dt.timedelta(days=offset) for offset in range(7)]


def _person(pid: int, name: str) -> dict:
    return {"id": pid, "name": name, "self": pid == 1}


def _sheet(cells: dict[dt.date, str] | None = None,
           people: tuple[str, ...] = ("Дарья Смирнова", "Сэм Второй")) -> list[list]:
    """Лист как его отдают люди: шапка с именами, даты в колонке B.

    В шапке два имени: `find_names_row` ищет строку, где подписей не меньше двух,
    иначе это не шапка, а случайная строка с одним словом.
    """
    header = ["", "", *people]
    values = [list(grid) for grid in (header,)]
    for day in DAYS:
        line = ["", day.strftime("%d.%m.%y"), *[""] * len(people)]
        text = (cells or {}).get(day)
        if text:
            line[2] = text
        values.append(line)
    return values


def _marks(pid: int, day: dt.date, state: str) -> dict:
    return {pid: {day.isoformat(): {"state": state}}}


def test_состояния_называются_словами():
    """Клетка несёт слово, а не цвет: «занят», «свободен», «не отмечено»."""
    assert month_grid.WORDS["busy"] == "занят"
    assert month_grid.WORDS["free"] == "свободен"
    assert month_grid.WORDS["empty"] == "не отмечено"

    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова")], marks={},
                            sheet_values=_sheet({DAYS[1]: "занят до 18:30"}))
    cell = body["cells"]["1"]["2026-11-10"]
    assert cell["state"] == "busy" and cell["word"] == "занят"
    assert cell["text"] == "занят до 18:30"
    assert body["cells"]["1"]["2026-11-11"]["word"] == "не отмечено"


def test_расхождение_показывается_а_не_правится():
    """Таблица и база разошлись — в ответе расхождение, значения листа не меняются."""
    values = _sheet({DAYS[1]: "занят"})
    pristine = copy.deepcopy(values)
    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова")],
                            marks=_marks(1, DAYS[1], "free"), sheet_values=values)

    cell = body["cells"]["1"]["2026-11-10"]
    assert cell["state"] == "busy" and cell["base"] == "free"
    assert cell["diff"] is True and cell["diff_text"]
    assert body["diffs"] == [{"day": "2026-11-10", "person": "Дарья Смирнова",
                              "sheet": "занят", "base": "свободен",
                              "text": "Дарья Смирнова: в таблице «занят», "
                                      "в базе «свободен»"}]
    assert values == pristine, "сборщик ответа не имеет права менять лист"


def test_совпадение_не_считается_расхождением():
    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова")],
                            marks=_marks(1, DAYS[1], "busy"),
                            sheet_values=_sheet({DAYS[1]: "занят до 18:30"}))
    assert body["diffs"] == []
    assert body["cells"]["1"]["2026-11-10"]["diff"] is False


def test_лист_недоступен_показываем_базу_и_говорим_об_этом():
    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова")],
                            marks=_marks(1, DAYS[1], "busy"),
                            sheet_values=None, sheet_reason="sheet_unavailable")

    assert body["sheet"] == {"available": False, "reason": "sheet_unavailable",
                             "days_missing": [day.isoformat() for day in DAYS],
                             "month_in_sheet": False}
    assert body["cells"]["1"]["2026-11-10"]["state"] == "busy"
    assert "показана база" in body["note"]


def test_месяца_нет_в_таблице_отдельный_случай():
    values = [["", "", "Дарья Смирнова"], ["", "01.10.26", "занят"]]
    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова")], marks={},
                            sheet_values=values)
    assert body["sheet"]["month_in_sheet"] is False
    assert "месяца в таблице нет" in body["note"]


def test_человека_нет_в_таблице_видно_по_строке():
    body = month_grid.build(month="2026-11", days=DAYS,
                            people=[_person(1, "Дарья Смирнова"), _person(2, "Новичок Без Колонки")],
                            marks=_marks(2, DAYS[2], "free"),
                            sheet_values=_sheet())
    assert body["people"][1]["sheet"] == month_grid.MISSING
    assert body["people"][1]["in_sheet"] is False
    assert body["cells"]["2"]["2026-11-11"]["state"] == "free", "по нему показана база"


def test_клетка_листа_разбирается_как_в_импорте():
    """«1»/«0» — как в раскрашенной сетке, мусор — «не разобрано», не догадка."""
    assert month_grid.sheet_cell("1") == ("busy", "1")
    assert month_grid.sheet_cell("0") == ("free", "0")
    assert month_grid.sheet_cell("") == ("empty", "")
    assert month_grid.sheet_cell("пока не понятно")[0] == "uncertain"
    assert month_grid.sheet_cell("отпуск")[0] == "unparsed", "неразборчивое не угадываем"

"""
Запись занятости в лист «Расписание студийцы» (FR-C9).

Проверяем привязку без Google: где шапка, где даты, в какую клетку писать, что
делать при неоднозначности и как снимок в служебной колонке отличает правку
человека от того, что записал бот.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from app.schedule_sheet import (
    SERVICE_HEADER,
    cell_a1,
    column_index,
    column_letter,
    find_day_rows,
    find_names_row,
    find_person_columns,
    find_service_column,
    merge_snapshot,
    parse_day_cell,
    plan_week,
    same_day,
    snapshot_get,
    snapshot_mismatches,
)


def sheet(days: list[datetime], person: str = "Виктор Иванов") -> list[list]:
    """Мини-лист как настоящий: шапка с именами, месяц, строки с датами в колонке B."""
    values = [["", "", "Решение", "Дарья Смирнова", person]]
    values.append(["", "ноябрь", "", "", ""])
    for offset, moment in enumerate(days):
        values.append([["пн", "вт", "ср", "чт", "пт", "сб", "вс"][offset % 7], moment, "", "", ""])
    return values


DAYS = [datetime(2026, 11, 2), datetime(2026, 11, 3), datetime(2026, 11, 4)]


def test_fr_c9_колонки_и_адреса_считаются_как_в_A1() -> None:
    """FR-C9: A1-адресация — единственное, что видит Sheets API; ошибка здесь пишет не туда."""
    assert [column_letter(i) for i in (1, 8, 11, 26, 27, 34)] == ["A", "H", "K", "Z", "AA", "AH"]
    assert [column_index(s) for s in ("A", "K", "AA", "AH")] == [1, 11, 27, 34]
    assert cell_a1(60, 11) == "K60"


def test_fr_c9_даты_читаются_из_колонки_B() -> None:
    """FR-C9: строки привязываются по настоящим датам листа, а не по порядку."""
    values = sheet(DAYS)
    rows = find_day_rows(values)
    assert rows[date(2026, 11, 2)] == 3
    assert rows[date(2026, 11, 4)] == 5
    assert parse_day_cell("08.11.2026") == date(2026, 11, 8)
    assert parse_day_cell("2026-11-08") == date(2026, 11, 8)
    assert parse_day_cell("не дата") is None


def test_fr_c9_план_пишет_день_и_снимок() -> None:
    """FR-C9: в клетку дня идёт канон, в служебную колонку — снимок для сверки."""
    values = sheet(DAYS)
    plan = plan_week(values, {date(2026, 11, 3): "занят до 18:30"}, 12, "Виктор Иванов")
    assert plan.problems == []
    assert plan.person_column == 5
    assert {(c.a1, c.text) for c in plan.cells} == {
        ("E4", "занят до 18:30"),
        (f"{column_letter(plan.service_column)}4", "12=занят до 18:30"),
        (f"{column_letter(plan.service_column)}1", SERVICE_HEADER),
    }


def test_fr_c9_человека_нет_в_шапке_значит_не_пишем() -> None:
    """FR-C9: нет колонки — расхождение для «Сверки», а не запись в чужую клетку."""
    plan = plan_week(sheet(DAYS), {date(2026, 11, 3): "занят"}, 12, "Неизвестный Человек")
    assert plan.cells == []
    assert any(problem.startswith("person_not_found") for problem in plan.problems)


def test_fr_c9_двух_одинаковых_имён_недостаточно_чтобы_писать() -> None:
    """FR-C9: неоднозначность — отказ: писать наугад в лист нельзя."""
    values = sheet(DAYS)
    values[0].append("Виктор Иванов")
    assert len(find_person_columns(values, 1, "Виктор Иванов")) == 2
    plan = plan_week(values, {date(2026, 11, 3): "занят"}, 12, "Виктор Иванов")
    assert any(problem.startswith("person_ambiguous") for problem in plan.problems)


def test_fr_c9_дня_нет_в_листе_значит_не_пишем() -> None:
    """FR-C9: неделя вне блоков листа — расхождение, а не запись в случайную строку."""
    plan = plan_week(sheet(DAYS), {date(2027, 1, 5): "занят"}, 12, "Виктор Иванов")
    assert any(problem.startswith("day_row_not_found") for problem in plan.problems)


def test_fr_c9_служебная_колонка_создаётся_справа_и_переиспользуется() -> None:
    """FR-C9: колонку `svoi` создаём один раз, дальше находим по подписи."""
    values = sheet(DAYS)
    column, created = find_service_column(values, 1)
    assert created is True and column == max(len(row) for row in values) + 2

    values[0] += [""] * (column - len(values[0]) - 1) + [SERVICE_HEADER]
    assert find_service_column(values, 1) == (column, False)


def test_fr_c9_снимок_правит_только_своего_человека() -> None:
    """FR-C9: снимок — общая колонка на всех, поэтому чужие записи не затираются."""
    existing = "12=занят до 18:30|15=свободен"
    assert snapshot_get(existing, 12) == "занят до 18:30"
    updated = merge_snapshot(existing, 14, "свободен с 19:00")
    assert updated == "12=занят до 18:30|14=свободен с 19:00|15=свободен"
    assert merge_snapshot(updated, 12, "") == "14=свободен с 19:00|15=свободен"


def test_fr_c9_правка_человека_видна_по_снимку() -> None:
    """FR-C9: клетка разошлась со снимком — человек правил руками, правку надо внести в базу."""
    values = sheet(DAYS)
    plan = plan_week(values, {date(2026, 11, 2): "занят до 18:30"}, 12, "Виктор Иванов")
    service = plan.service_column
    # применяем план к «листу» и правим клетку дня как человек
    for cell in plan.cells:
        letters = "".join(ch for ch in cell.a1 if ch.isalpha())
        row = int("".join(ch for ch in cell.a1 if ch.isdigit()))
        while len(values) < row:
            values.append([])
        line = values[row - 1]
        while len(line) < column_index(letters):
            line.append("")
        line[column_index(letters) - 1] = cell.text
    assert snapshot_mismatches(values, 12, "Виктор Иванов") == []

    # «свободен с 19:00» — та же занятость, что «занят до 18:30» (минуты округляются
    # в сторону занятости), поэтому расхождением не считается. Меняем по-настоящему:
    values[2][4] = "свободен с 20:00"
    found = snapshot_mismatches(values, 12, "Виктор Иванов")
    assert found == [{"day": "2026-11-02", "cell": "свободен с 20:00",
                      "bot": "занят до 18:30", "row": 3}]
    assert service  # колонка найдена, а не создана заново


def test_fr_c9_сверка_сравнивает_смысл_а_не_буквы():
    """FR-C9: «занят до 19:00» и «свободен с 19:00» — одна занятость, а не расхождение."""
    assert same_day("занят до 19:00", "свободен с 19:00") is True
    assert same_day("занята до 18:30", "занят до 18:30") is True       # род не важен
    assert same_day("занят до 19.00", "занят до 19:00") is True        # формат не важен


def test_fr_c9_сверка_видит_настоящую_разницу():
    """FR-C9: другая граница, другой статус или новый комментарий — это расхождение."""
    assert same_day("занят до 19:00", "занят до 20:00") is False
    assert same_day("занят до 19:00", "свободен") is False
    assert same_day("занят (не будет в Самаре)", "занят") is False
    assert same_day("абракадабра", "занят") is False

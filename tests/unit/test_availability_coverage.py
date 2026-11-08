"""
Покрытие занятости: можно ли верить своду (FR-C7).

Проверяем то, из-за чего свод обманывает: молчание про неотметившихся читается как
«свободен». Порог и формулировка должны быть однозначными.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.availability_coverage import THRESHOLD, coverage, plural, week_title


@pytest.mark.parametrize(
    ("expected", "marked", "trustworthy"),
    [
        (17, 17, True),
        (17, 14, True),      # 0.82 — выше порога
        (17, 13, False),     # 0.76 — ниже порога
        (5, 4, True),        # 0.80 — ровно порог, верить можно
        (0, 0, False),       # некого спрашивать
    ],
)
def test_fr_c7_порог_решает_можно_ли_верить_своду(expected, marked, trustworthy):
    """FR-C7: ниже 80% отметившихся свод неполный и об этом говорится прямо."""
    week = coverage(expected, marked)
    assert week.trustworthy is trustworthy
    assert week.expected == expected and week.marked == marked


def test_fr_c7_строка_называет_числа_и_нехватку():
    """FR-C7: человек видит, сколько отметилось и сколько не хватает до порога."""
    low = coverage(17, 6).line()
    assert "6 из 17" in low and "не хватает 11" in low and "80%" in low
    high = coverage(17, 16).line()
    assert "16 из 17" in high and "верить" in high


def test_fr_c7_пустая_студия_не_даёт_ложного_оптимизма():
    """FR-C7: ноль активных людей — не «100% покрытие», а «планировать не по кому»."""
    week = coverage(0, 0)
    assert week.ratio == 0.0 and week.trustworthy is False
    assert "планировать не по кому" in week.line()


def test_fr_c7_отметок_больше_чем_людей_не_ломает_счёт():
    """FR-C7: лишние отметки (архив, импорт) не дают покрытия больше единицы."""
    week = coverage(10, 12)
    assert week.ratio == 1.2 and week.trustworthy is True
    assert week.missing == 0


def test_fr_c7_неделя_подписывается_диапазоном():
    """FR-C7: строка о покрытии называет неделю, иначе непонятно, о чём она."""
    assert week_title(date(2026, 11, 9)) == "09 — 15 ноября"
    assert week_title(date(2026, 11, 30)) == "30 ноября — 06 декабря"
    assert THRESHOLD == 0.8


def test_fr_c7_неподключённые_называются_отдельно():
    """FR-C7: pending-люди не портят процент, но и не исчезают из строки.

    «3 из 4» на базе, где половина труппы ещё не зашла в бота, выглядит как
    хорошая новость. Поэтому о неподключённых говорим прямо.
    """
    week = coverage(4, 3, not_connected=13)
    line = week.line()
    assert "3 из 4" in line
    assert "Ещё 13 человек не подключены" in line
    assert week.not_connected == 13


def test_fr_c7_числа_склоняются_по_русски():
    """FR-C7: «2 человека», а не «2 человек» — ошибка в тексте видна раньше, чем в цифрах."""
    assert [plural(n, "человек", "человека", "человек") for n in (1, 2, 5, 11, 21)] == [
        "человек", "человека", "человек", "человек", "человек"]

"""
«Обычная неделя»: шаблон занятости по дням недели (FR-C10).

Проверяем то, из-за чего шаблон может навредить: он не должен затирать уже
отмеченные дни и не должен терять минуты («занят до 18:30» — не «до 19:00»).
"""

from __future__ import annotations

from datetime import date

import pytest
from app.availability_card import DayCard
from app.availability_template import TemplateDay, fill, from_week, to_week


def test_fr_c10_неделя_становится_шаблоном_по_дням_недели():
    """FR-C10: шаблон — это по одному дню на день недели, а не дата."""
    cards = {
        date(2026, 11, 2): DayCard("busy", None, 18 * 60 + 30),     # понедельник
        date(2026, 11, 4): DayCard("free"),                          # среда
    }
    template = from_week(cards)
    assert [item.weekday for item in template] == [0, 2]
    assert template[0].card.to_min == 18 * 60 + 30     # минуты не теряются
    assert template[1].card.status == "free"


def test_fr_c10_шаблон_разворачивается_в_конкретные_даты():
    """FR-C10: среда в шаблоне — это среда той недели, которую просят."""
    template = [TemplateDay(2, DayCard("busy", None, 19 * 60))]
    week = to_week(template, date(2026, 11, 5))        # четверг той же недели
    assert list(week) == [date(2026, 11, 4)]


def test_fr_c10_заполнение_не_затирает_уже_отмеченные_дни():
    """FR-C10: шаблон не съедает то, что человек уже отметил руками."""
    template = [TemplateDay(0, DayCard("free")), TemplateDay(2, DayCard("busy"))]
    existing = {date(2026, 11, 2): DayCard("busy", None, 16 * 60),
                date(2026, 11, 3): None}
    write, skipped = fill(date(2026, 11, 2), template, existing)
    assert list(write) == [date(2026, 11, 4)]
    assert skipped == [date(2026, 11, 2)]


def test_fr_c10_с_разрешением_перезаписывает_всё():
    """FR-C10: overwrite — осознанное действие, тогда пишем все дни шаблона."""
    template = [TemplateDay(0, DayCard("free")), TemplateDay(2, DayCard("busy"))]
    existing = {date(2026, 11, 2): DayCard("busy", None, 16 * 60)}
    write, skipped = fill(date(2026, 11, 2), template, existing, overwrite=True)
    assert sorted(write) == [date(2026, 11, 2), date(2026, 11, 4)]
    assert skipped == []


def test_fr_c10_неделя_в_шаблоне_может_быть_неполной():
    """FR-C10: отметить по шаблону два дня — нормально, остальные остаются пустыми."""
    template = [TemplateDay(5, DayCard("busy"))]
    write, skipped = fill(date(2026, 11, 2), template, {}, overwrite=False)
    assert list(write) == [date(2026, 11, 7)] and skipped == []


def test_fr_c10_день_недели_проверяется():
    """FR-C10: «восьмой день недели» — ошибка, а не тихий сдвиг на понедельник."""
    with pytest.raises(ValueError):
        TemplateDay(7, DayCard("free"))

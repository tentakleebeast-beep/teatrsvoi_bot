"""Категории формы операции: доход и расход не смешиваются.

Обоснование (Ш-51): на проде 30.10.2026 форма показывала общий список, и человек
записал доход 200 ₽ в расходную категорию «Коммунальные платежи». Инвариант I3
это поймал, но уже после записи — и в базе, и в листе.
"""

from __future__ import annotations

ROWS = [
    {"name": "Аренда помещения", "direction": "expense"},
    {"name": "Донат", "direction": "income"},
    {"name": "Взносы студийцев", "direction": "income"},
    {"name": "", "direction": "income"},          # пустая запись от старой проверки
    {"name": "Странная", "direction": "unknown"},  # неизвестное направление — в расход
]


def test_категории_разложены_по_направлению():
    from app.api.budget import group_categories

    grouped = group_categories(ROWS)
    assert grouped["income"] == ["Донат", "Взносы студийцев"]
    assert "Аренда помещения" in grouped["expense"]
    assert "Донат" not in grouped["expense"]


def test_пустое_имя_категории_не_попадает_в_список():
    from app.api.budget import group_categories

    grouped = group_categories(ROWS)
    assert "" not in grouped["income"] + grouped["expense"]


def test_неизвестное_направление_считается_расходом():
    from app.api.budget import group_categories

    assert "Странная" in group_categories(ROWS)["expense"]

"""Проверка списка типов до записи: таблица ошибок §6.3 (docs/development/190 §6.3).

Обоснование: FR-D3, `docs/development/190` §6.3, AC-17 (дубль буквы, изменённая буква,
цвет 12). Главное обещание — «ничего не сохранено»: проверка идёт до записи, поэтому
любая ошибка оставляет базу нетронутой, а не наполовину обновлённой.
"""

from __future__ import annotations

import pytest
from app import event_types

SEED = (
    {"code": "rental", "letter": "А", "title": "Аренда", "color_id": 10,
     "sort_order": 10, "is_active": True},
    {"code": "show", "letter": "С", "title": "Спектакль", "color_id": 11,
     "sort_order": 20, "is_active": True},
    {"code": "rehearsal", "letter": "Р", "title": "Репетиция", "color_id": 9,
     "sort_order": 30, "is_active": True},
    {"code": "training", "letter": "Т", "title": "Тренинг", "color_id": 5,
     "sort_order": 40, "is_active": True},
    {"code": "project", "letter": "П", "title": "Проект", "color_id": None,
     "sort_order": 50, "is_active": False},
    {"code": "meeting", "letter": None, "title": "Встреча", "color_id": 8,
     "sort_order": 90, "is_active": False},
    {"code": "other", "letter": None, "title": "Другое", "color_id": 8,
     "sort_order": 91, "is_active": False},
)


class FakeConn:
    """Соединение без БД: словарь типов и журнал записей."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, sql, params=None):
        self.calls.append(" ".join(str(sql).split()).lower())
        return self

    def fetchall(self):
        return [dict(item) for item in SEED]

    def fetchone(self):
        return None

    def writes(self) -> list[str]:
        return [sql for sql in self.calls
                if sql.startswith(("update ", "insert ", "delete "))]


def _item(**changes) -> dict:
    base = {"code": "show", "letter": "С", "title": "Спектакль", "color_id": 11,
            "sort_order": 20, "is_active": True}
    base.update(changes)
    return base


@pytest.mark.parametrize("item,expected,status", [
    # буква не одна заглавная кириллическая
    (_item(letter="с"), "letter_bad", 400),
    (_item(letter="СЫ"), "letter_bad", 400),
    (_item(letter="S"), "letter_bad", 400),
    # изменённая буква существующего типа (I-2)
    (_item(letter="Ж"), "letter_immutable", 409),
    # дубль буквы: «Ж» у нового типа уже занята в этом же списке
    ({"code": None, "letter": "С", "title": "Концерт", "color_id": 3,
      "sort_order": 60, "is_active": True}, "letter_taken", 409),
    # название пустое после strip или длиннее 40 знаков
    (_item(title="   "), "title_bad", 400),
    (_item(title="Я" * 41), "title_bad", 400),
    # цвет вне 1…11
    (_item(color_id=12), "color_bad", 400),
    (_item(color_id=0), "color_bad", 400),
    # активный тип без цвета
    ({"code": None, "letter": "К", "title": "Концерт", "color_id": None,
      "sort_order": 60, "is_active": True}, "color_required", 400),
    # порядок вне 0…9999
    (_item(sort_order=10_000), "order_bad", 400),
    # тип с несуществующим кодом
    (_item(code="unknown"), "code_unknown", 404),
])
def test_таблица_ошибок(item, expected, status) -> None:
    """§6.3: каждая ошибка со своим кодом и статусом; поле названо."""
    conn = FakeConn()
    problems = event_types.validate(conn, [item], apply_mode="new")
    assert [problem["code"] for problem in problems] == [expected], problems
    assert problems[0]["status"] == status
    assert problems[0]["index"] == 0


def test_без_режима_перекраски_список_не_принимается() -> None:
    """`apply` обязателен: значения по умолчанию у сервера нет (§6.3)."""
    conn = FakeConn()
    problems = event_types.validate(conn, [_item()], apply_mode=None)
    assert [problem["code"] for problem in problems] == ["apply_required"]


def test_ошибка_ничего_не_сохраняет() -> None:
    """Любая ошибка — база не тронута: проверка стоит до записи, а не после неё."""
    conn = FakeConn()
    bad = [_item(), _item(code="rehearsal", letter="Ж", title="Репетиция")]
    problems = event_types.validate(conn, bad, apply_mode="all")
    assert problems and problems[0]["code"] == "letter_immutable"
    assert conn.writes() == [], "проверка успела записать в базу"

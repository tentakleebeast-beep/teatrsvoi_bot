"""Набор адресатов: объединение правил + галочки − исключения (AC-3).

Проверяется чистый расчёт (`poll_store.audience_ids`), без БД: «все организаторы,
кроме меня», пересечение роли и спектакля, галочка на человека вне роли.

Обоснование: FR-G18, AC-3, `docs/development/162-bot-polls.md` §14.2 (audience).
"""

from __future__ import annotations

import pytest
from app import poll_store

PEOPLE = [
    {"id": 1, "roles": ["admin"]},
    {"id": 2, "roles": ["org"]},
    {"id": 3, "roles": ["org"]},
    {"id": 4, "roles": ["troupe"]},
    {"id": 5, "roles": ["master"]},
]
PROJECTS = {10: [3, 4], 11: []}


def test_правило_роли_и_галочка_складываются():
    ids = poll_store.audience_ids(rules=[{"kind": "role", "role": "org"}],
                                  include=[5], exclude=[],
                                  people=PEOPLE, project_members=PROJECTS)
    assert ids == [2, 3, 5]


def test_исключение_сильнее_любого_правила():
    """«Все организаторы, кроме меня» — нормальная просьба, а не край."""
    ids = poll_store.audience_ids(rules=[{"kind": "role", "role": "org"}],
                                  include=[3], exclude=[2],
                                  people=PEOPLE, project_members=PROJECTS)
    assert ids == [3]


def test_спектакль_и_роль_дают_объединение_без_дублей():
    ids = poll_store.audience_ids(
        rules=[{"kind": "role", "role": "troupe"},
               {"kind": "project", "project_id": 10}],
        include=[], exclude=[], people=PEOPLE, project_members=PROJECTS)
    assert ids == [3, 4]


def test_все_активные_берёт_действующих_целиком():
    ids = poll_store.audience_ids(rules=[{"kind": "all_active"}], include=[], exclude=[1],
                                  people=PEOPLE, project_members=PROJECTS)
    assert ids == [2, 3, 4, 5]


def test_повторный_расчёт_даёт_тот_же_набор():
    """Счётчик до отправки и снимок при отправке считаются одной функцией (AC-3)."""
    rules = [{"kind": "role", "role": "org"}]
    first = poll_store.audience_ids(rules=rules, include=[4], exclude=[2],
                                    people=PEOPLE, project_members=PROJECTS)
    second = poll_store.audience_ids(rules=rules, include=[4], exclude=[2],
                                     people=PEOPLE, project_members=PROJECTS)
    assert first == second == [3, 4]


def test_неизвестная_роль_и_пустой_спектакль_дают_код_контракта():
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.audience_ids(rules=[{"kind": "role", "role": "student"}],
                                include=[], exclude=[], people=PEOPLE,
                                project_members=PROJECTS)
    assert error.value.code == "bad_rule"
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.audience_ids(rules=[{"kind": "project", "project_id": None}],
                                include=[], exclude=[], people=PEOPLE,
                                project_members=PROJECTS)
    assert error.value.code == "bad_rule"

"""`editable` карточки — та же функция, что проверяет перенос (docs/development/190 §3.3).

Обоснование: FR-D19, `docs/development/190` §3.3, AC-5…AC-7.
Правило одно: карточка не может показать кнопку «Перенести» там, где
`POST /tools/events/move` ответит 403. Поэтому `_editable` зовёт `_can_move`,
и подмена этой функции меняет оба ответа сразу.
"""

from __future__ import annotations

import pytest
from app.api import events as events_api
from app.api import webapp_events

OWNER = {"id": 7, "caps": [], "roles": ["troupe"]}
FOREIGN = {"id": 9, "caps": [], "roles": ["troupe"]}
ADMIN = {"id": 1, "caps": ["*"], "roles": ["admin"]}


@pytest.mark.parametrize("viewer,created_by,editable,reason", [
    (OWNER, 7, True, None),                       # создатель
    (ADMIN, 7, True, None),                       # администратор — любое
    (FOREIGN, 7, False, "not_your_event"),        # чужое событие
])
def test_создатель_и_администратор_переносят(viewer, created_by, editable, reason) -> None:
    """`_can_move` — одно решение для карточки и переноса."""
    assert webapp_events._editable(True, "confirmed", created_by, "draft", False,
                                   viewer) == (editable, reason)


@pytest.mark.parametrize("case,reason", [
    ("cancelled", "cancelled"),
    ("read", "live_calendar"),
    ("human", "human_event"),
    ("missing", "plan_missing"),
])
def test_причины_отказа_по_порядку(case, reason) -> None:
    """§3.3: причины не складываются — отвечает первая подходящая.

    У людского события причина одна и в боевом календаре (`human_event`, AC-5):
    оно создано в Google, и «боевой календарь» тут ничего не добавляет.
    """
    viewer = OWNER
    if case == "cancelled":
        result = webapp_events._editable(True, "cancelled", 7, "draft", True, viewer)
    elif case == "read":
        result = webapp_events._editable(True, "confirmed", 7, "read", False, viewer)
    elif case == "human":
        result = webapp_events._editable(False, None, None, "read", False, viewer)
    else:
        result = webapp_events._editable(True, None, None, "draft", False, viewer)
    assert result == (False, reason)


def test_боевой_календарь_закрыт_для_всех(monkeypatch) -> None:
    """AC-7: `calendar=read` — всегда `false` с `live_calendar`, даже админу."""
    for viewer in (OWNER, ADMIN):
        assert webapp_events._editable(True, "confirmed", 7, "read", False, viewer) == \
            (False, "live_calendar")


def test_подмена_одной_функции_меняет_оба_ответа(monkeypatch) -> None:
    """AC-5/§3.3: карточка и перенос спрашивают одно и то же место.

    Если правило переноса поменять (ролевая карта `t_4d081225`), карточка обязана
    поменяться вместе с ним — иначе кнопка снова окажется там, где придёт 403.
    """
    assert webapp_events._editable(True, "confirmed", 7, "draft", False, OWNER) == (True, None)
    monkeypatch.setattr(events_api, "_can_move", lambda plan, who: False)
    assert webapp_events._editable(True, "confirmed", 7, "draft", False, OWNER) == \
        (False, "not_your_event")

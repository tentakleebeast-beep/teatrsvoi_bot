"""Граница записи: протокол привязки не пускает туда, где ключа записи нет.

Обоснование: docs/development/186 §2.2 (реестр), §4.3 (шаги протокола), §9 (коды).
Граница проверяется исполнением `open_sheet`: живой лист и боевой календарь отказывают
до любого обращения к Google, а не «по договорённости в коде».
"""

from __future__ import annotations

import pytest
from app import core, google_targets, sheet_binding
from app.errors import ApiError


class FakeConn:
    """Соединение без БД: отдаёт пустые ответы, запоминает запросы."""

    def __init__(self, binding=None) -> None:
        self.binding = binding
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or ()))
        return self

    def fetchone(self):
        return self.binding


class BrokenService:
    """Любое обращение к Google — провал теста: граница должна отказать раньше."""

    def spreadsheets(self):
        raise AssertionError("до Google дело дойти не должно")

    def events(self):
        raise AssertionError("до Google дело дойти не должно")


def test_живой_лист_не_пишется(monkeypatch):
    """`schedule.human` — источник чтения: попытка записи отказывает 403."""
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "live-sheet")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("schedule.human", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 403
    assert caught.value.code == "write_target_refused"


def test_боевой_календарь_не_пишется(monkeypatch):
    """`calendar.read` — боевой календарь: ключа записи у него не существует."""
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("calendar.read", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 403
    assert caught.value.code == "write_target_refused"


def test_ненастроенный_объект_отказывает_503(monkeypatch):
    """Ключ есть в реестре, id пуст — `write_target_unset`, Google не вызывается."""
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("schedule.test", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 503
    assert caught.value.code == "write_target_unset"


def test_без_строки_привязки_запись_остановлена(monkeypatch):
    """Объект настроен, но `sheet_binding` пуст — `binding_missing`, а не догадка."""
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "bot-budget")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("budget.transactions", conn=FakeConn(),
                                 service=BrokenService())
    assert caught.value.status_code == 409
    assert caught.value.code == "binding_missing"


def test_список_объектов_записи_не_меняется_режимом(monkeypatch):
    """Дизайн D7: набор объектов записи одинаков в `transitional` и `target`."""
    for mode in google_targets.MODES:
        monkeypatch.setattr(google_targets, "google_mode", lambda _conn=None, m=mode: m)
        for key in google_targets.WRITE_TARGETS:
            assert google_targets.allowed_for_write(key)
        assert not google_targets.allowed_for_write("calendar.read")

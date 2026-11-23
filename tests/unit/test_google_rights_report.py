"""Отчёт о правах SA: роль меряется требуемой операцией (186 §6, AC-13).

Обоснование: docs/development/186 §6 (права сервисного аккаунта), AC-13; docs/development/190 §10.
Возврат ревью 23.11.2026: `acl.list` читательским клиентом отвечает 403 потому, что аккаунт
не владелец календаря, а не потому, что прав нет; отчёт показывал расхождение на объектах,
где бот работает. Здесь закреплено: reader — `events.list`, writer — `acl.list` пишущим
клиентом либо пробная вставка с удалением, таблицы — `capabilities` из Drive.
"""

from __future__ import annotations

import sys

import google_rights as rights
from app import core, google_targets


class FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeHttpError(Exception):
    """Отказ Google в форме `HttpError`: код живёт в `resp.status`."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = FakeResp(status)


class FakeRequest:
    def __init__(self, answer=None, exc: Exception | None = None, calls: list | None = None,
                 label: str = "") -> None:
        self._answer = answer
        self._exc = exc
        self._calls = calls if calls is not None else []
        self._label = label

    def execute(self):
        self._calls.append(self._label)
        if self._exc is not None:
            raise self._exc
        return self._answer or {}


class FakeEvents:
    def __init__(self, calls: list, list_exc: Exception | None = None,
                 insert_exc: Exception | None = None, delete_exc: Exception | None = None):
        self.calls = calls
        self.list_exc = list_exc
        self.insert_exc = insert_exc
        self.delete_exc = delete_exc
        self.bodies: list[dict] = []

    def list(self, **kwargs):
        return FakeRequest({"items": []}, self.list_exc, self.calls, "events.list")

    def insert(self, **kwargs):
        self.bodies.append(kwargs.get("body") or {})
        return FakeRequest({"id": "probe-1"}, self.insert_exc, self.calls, "events.insert")

    def delete(self, **kwargs):
        return FakeRequest({}, self.delete_exc, self.calls, "events.delete")


class FakeAcl:
    def __init__(self, calls: list, exc: Exception | None = None, items: list | None = None):
        self.calls = calls
        self.exc = exc
        self.items = items or []

    def list(self, **kwargs):
        return FakeRequest({"items": self.items}, self.exc, self.calls, "acl.list")


class FakeCalendar:
    def __init__(self, calls: list, acl=None, events=None):
        self.calls = calls
        self._acl = acl or FakeAcl(calls)
        self._events = events or FakeEvents(calls)

    def acl(self):
        return self._acl

    def events(self):
        return self._events


def test_читатель_календаря_меряется_events_list(monkeypatch):
    """Reader: 403 у acl.list роли не решает — пробуется то, что делает сверка."""
    calls: list[str] = []
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "cal-read")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar", lambda: FakeCalendar(calls))
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.read")

    assert row["ok"] is True, row
    assert row["role"] == rights.READER_PROBE
    assert "events.list" in calls
    assert "acl.list" not in calls


def test_читатель_календаря_без_доступа_называет_операцию(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "cal-read")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar",
                        lambda: FakeCalendar(calls, events=FakeEvents(calls, list_exc=FakeHttpError(403))))
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.read")

    assert row["ok"] is False
    assert "events.list: 403" in row["error"]


def test_писатель_календаря_не_владелец_меряется_вставкой(monkeypatch):
    """Writer: acl.list 403 = «не владелец», роль подтверждает insert+delete."""
    calls: list[str] = []
    events = FakeEvents(calls)
    calendar = FakeCalendar(calls, acl=FakeAcl(calls, exc=FakeHttpError(403)), events=events)
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.draft")

    assert row["ok"] is True, row
    assert row["role"] == rights.WRITER_PROBE
    assert calls == ["acl.list", "events.insert", "events.delete"]
    assert events.bodies[0]["extendedProperties"]["private"]["svoi_kind"] == rights.PROBE_KIND


def test_пробное_событие_не_удалено_остаётся_предупреждением(monkeypatch):
    calls: list[str] = []
    calendar = FakeCalendar(calls, acl=FakeAcl(calls, exc=FakeHttpError(403)),
                            events=FakeEvents(calls, delete_exc=FakeHttpError(500)))
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.draft")

    assert row["ok"] is True
    assert any("probe-1" in warning for warning in row["warnings"])


def test_писатель_без_приватных_событий_считается_писателем(monkeypatch):
    """Живой контур отдаёт `writerWithoutPrivateAccess`; своим событием такая роль правит."""
    calls: list[str] = []
    acl = FakeAcl(calls, items=[{"role": "writerWithoutPrivateAccess",
                                 "scope": {"type": "user", "value": "sa@example.com"}}])
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar_write", lambda: FakeCalendar(calls, acl=acl))
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.draft")

    assert row["ok"] is True, row
    assert "events.insert" not in calls


def test_писатель_календаря_виден_по_acl(monkeypatch):
    """Когда acl.list открыт (SA владелец), проба не нужна и событие не создаётся."""
    calls: list[str] = []
    acl = FakeAcl(calls, items=[{"role": "owner",
                                 "scope": {"type": "user", "value": "sa@example.com"}}])
    calendar = FakeCalendar(calls, acl=acl)
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "cal-draft")
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")

    row = rights._check("calendar.draft")

    assert row["ok"] is True
    assert row["role"] == "owner"
    assert "events.insert" not in calls


class FakeFiles:
    def __init__(self, calls: list, caps: dict | None):
        self.calls = calls
        self.caps = caps

    def get(self, **kwargs):
        return FakeRequest({"capabilities": self.caps} if self.caps is not None else {},
                           calls=self.calls, label="files.get")


class FakePermissions:
    def __init__(self, calls: list, exc: Exception | None = None):
        self.calls = calls
        self.exc = exc

    def list(self, **kwargs):
        return FakeRequest({"permissions": []}, self.exc, self.calls, "permissions.list")


class FakeDrive:
    def __init__(self, calls: list, caps: dict | None, perm_exc: Exception | None = None):
        self.calls = calls
        self._files = FakeFiles(calls, caps)
        self._permissions = FakePermissions(calls, perm_exc)

    def files(self):
        return self._files

    def permissions(self):
        return self._permissions


def test_таблица_редактор_меряется_capabilities(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "budget-sheet")
    monkeypatch.setattr(core, "_drive_read",
                        lambda: FakeDrive(calls, {"canEdit": True}, perm_exc=FakeHttpError(403)))

    row = rights._check("budget.transactions")

    assert row["ok"] is True, row
    assert row["role"] == google_targets.ROLE_WRITER
    assert "files.get" in calls


def test_таблица_читатель_без_права_правки(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "human-sheet")
    monkeypatch.setattr(core, "_drive_read", lambda: FakeDrive([], {"canEdit": False}))

    row = rights._check("schedule.human")

    assert row["ok"] is True
    assert row["role"] == google_targets.ROLE_READER


def test_скрипты_ставят_корень_проекта_в_sys_path():
    """Точка входа `python scripts/<имя>.py` не зависит от PYTHONPATH (AC-5, AC-13)."""
    import google_seed_bindings as seed

    assert str(rights.ROOT) in sys.path
    assert str(seed.ROOT) in sys.path

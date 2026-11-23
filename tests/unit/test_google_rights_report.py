"""Отчёт о правах SA: роль меряется требуемой операцией (186 §6, AC-13).

Обоснование: docs/development/186 §6 (права сервисного аккаунта), AC-13; docs/development/190 §10.
Возврат ревью 23.11.2026: `acl.list` читательским клиентом отвечает 403 потому, что аккаунт
не владелец календаря, а не потому, что прав нет; отчёт показывал расхождение на объектах,
где бот работает. Здесь закреплено: reader — `events.list`, writer — `acl.list` пишущим
клиентом либо пробная вставка с удалением, таблицы — `capabilities` из Drive.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager

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
        self.inserted_calendars: list[str] = []

    def list(self, **kwargs):
        return FakeRequest({"items": []}, self.list_exc, self.calls, "events.list")

    def insert(self, **kwargs):
        self.bodies.append(kwargs.get("body") or {})
        self.inserted_calendars.append(str(kwargs.get("calendarId") or ""))
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


class FakeCalendarList:
    """`calendarList.get`: отдаёт `accessRole` — права аккаунта на календарь."""

    def __init__(self, calls: list, access_role: str = "writer",
                 exc: Exception | None = None):
        self.calls = calls
        self.access_role = access_role
        self.exc = exc

    def get(self, **kwargs):
        answer = {"accessRole": self.access_role} if self.access_role else {}
        return FakeRequest(answer, self.exc, self.calls, "calendarList.get")


class FakeCalendar:
    def __init__(self, calls: list, acl=None, events=None, calendar_list=None):
        self.calls = calls
        self._acl = acl or FakeAcl(calls)
        self._events = events or FakeEvents(calls)
        self._calendar_list = calendar_list or FakeCalendarList(calls)

    def acl(self):
        return self._acl

    def events(self):
        return self._events

    def calendarList(self):
        return self._calendar_list


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


def test_боевой_календарь_не_пробуется_вставкой(monkeypatch):
    """V-120 б: для `calendar.live` роль берётся из `accessRole`, пробы нет.

    В боевой календарь нельзя вставлять проверочное событие — там работают люди.
    Поэтому роль подтверждает `calendarList.get`, а `events.insert` не вызывается.
    """
    calls: list[str] = []
    acl = FakeAcl(calls, exc=FakeHttpError(403))
    calendar = FakeCalendar(calls, acl=acl,
                            calendar_list=FakeCalendarList(calls, access_role="writer"))
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "cal-live")
    monkeypatch.setattr(core, "_calendar", lambda: calendar)
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")
    monkeypatch.setattr(google_targets, "google_mode",
                        lambda _conn=None: google_targets.MODE_TARGET)

    row = rights._check("calendar.live")

    assert row["ok"] is True, row
    assert row["role"] == "writer"
    assert calls == ["acl.list", "calendarList.get"], calls
    assert "events.insert" not in calls
    assert any(rights.ACL_PROBE in warning for warning in row["warnings"])


def test_боевой_календарь_читателю_не_хватает_прав(monkeypatch):
    """`accessRole=reader` у боевого календаря — расхождение, а не «ok»."""
    calls: list[str] = []
    calendar = FakeCalendar(calls, acl=FakeAcl(calls, exc=FakeHttpError(403)),
                            calendar_list=FakeCalendarList(calls, access_role="reader"))
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "cal-live")
    monkeypatch.setattr(core, "_calendar", lambda: calendar)
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")
    monkeypatch.setattr(google_targets, "google_mode",
                        lambda _conn=None: google_targets.MODE_TARGET)

    row = rights._check("calendar.live")

    assert row["ok"] is False, row
    assert row["role"] == "reader"
    assert "events.insert" not in calls


def test_боевой_календарь_в_переходном_режиме_пропускается(monkeypatch):
    """Ключ `calendar.live` действует только в `target`: в переходном — строка отчёта."""
    calls: list[str] = []
    monkeypatch.setattr(google_targets, "google_mode",
                        lambda _conn=None: google_targets.MODE_TRANSITIONAL)

    row = rights._check("calendar.live")

    assert row["ok"] is True, row
    assert any("только в целевом режиме" in warning for warning in row["warnings"]), row
    assert calls == [], "в переходном режиме отчёт ходил в Google"


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


class FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Соединение отчёта: знает только строку `app_setting.google.mode`."""

    def __init__(self, mode: str | None) -> None:
        self.mode = mode

    def execute(self, query, params=None):
        assert "google.mode" in query, query
        return FakeResult({"value": self.mode} if self.mode is not None else None)


class FakePool:
    """Пул psycopg: закрыт, пока его не открыли — как настоящий `core.pool`.

    Настоящий пул создаётся `open=False` и на `connection()` поднимает `PoolClosed`;
    двойник ведёт себя так же, иначе тест не отличает «пул открыт» от «пул забыли открыть».
    """

    def __init__(self, mode: str | None) -> None:
        self.mode = mode
        self.opened = False
        self.closed = False
        self.connections = 0

    def open(self) -> None:
        self.opened = True
        self.closed = False

    def close(self) -> None:
        self.opened = False
        self.closed = True

    @contextmanager
    def connection(self):
        if not self.opened:
            raise RuntimeError("the pool 'pool-1' is not open yet")
        self.connections += 1
        yield FakeConn(self.mode)


def _install_google(monkeypatch, calls: list, access_role: str = "writer") -> FakeEvents:
    """Подменить Google-клиенты и идентификаторы так, чтобы отчёт прошёл целиком."""
    events = FakeEvents(calls)
    calendar = FakeCalendar(
        calls, acl=FakeAcl(calls, exc=FakeHttpError(403)), events=events,
        calendar_list=FakeCalendarList(calls, access_role=access_role))
    monkeypatch.setattr(core, "_calendar", lambda: calendar)
    monkeypatch.setattr(core, "_calendar_write", lambda: calendar)
    monkeypatch.setattr(core, "_drive_read", lambda: FakeDrive(calls, {"canEdit": True}))
    monkeypatch.setattr(rights, "_service_account_email", lambda: "sa@example.com")
    for name, value in (
        ("CALENDAR_ID_READ", "cal-live"), ("CALENDAR_ID_DRAFT", "cal-draft"),
        ("BUDGET_SPREADSHEET_ID", "budget"), ("SCHEDULE_SPREADSHEET_ID", "sched"),
        ("SCHEDULE_TEST_SPREADSHEET_ID", "test"), ("SCHEDULE_HUMAN_SPREADSHEET_ID", "human"),
        ("FINANCE_SOURCE_ID", "fin"), ("DRIVE_ROOT_ID", "drive"),
    ):
        monkeypatch.setattr(core, name, value)
    return events


def test_точка_входа_открывает_пул_и_исполняется_целиком(monkeypatch, capsys):
    """Возврат ревью 23.11.2026: `main` не открывал пул и падал на `calendar.live`.

    Проверка исполняет точку входа (`main(["--json"])`), а не `_check`: прежний тест
    подменял `google_mode`, поэтому закрытый пул и «the pool is not open yet» в живом
    запуске не видел ни один прогон. В переходном режиме строка `calendar.live` —
    пропуск с предупреждением, отчёт зелёный, пишущей пробы нет.
    """
    pool = FakePool(google_targets.MODE_TRANSITIONAL)
    monkeypatch.setattr(core, "pool", pool)
    monkeypatch.setattr(core, "GOOGLE_SA_JSON", "/tmp/sa.json")
    calls: list[str] = []
    events = _install_google(monkeypatch, calls)

    code = rights.main(["--json"])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "not open" not in out
    assert pool.connections == 1, "режим читался не в соединении точки входа"
    assert pool.closed is True, "пул остался открытым после отчёта"
    rows = {row["key"]: row for row in json.loads(out)}
    live = rows[google_targets.LIVE_CALENDAR]
    assert live["ok"] is True and live["role"] is None, live
    assert any("только в целевом режиме" in warning for warning in live["warnings"]), live
    assert "cal-live" not in events.inserted_calendars, "в боевой календарь ходили пробой"


def test_точка_входа_в_целевом_режиме_берёт_роль_из_access_role(monkeypatch, capsys):
    """В `target` строка `calendar.live` получает роль из `accessRole`, без вставки."""
    pool = FakePool(google_targets.MODE_TARGET)
    monkeypatch.setattr(core, "pool", pool)
    monkeypatch.setattr(core, "GOOGLE_SA_JSON", "/tmp/sa.json")
    calls: list[str] = []
    events = _install_google(monkeypatch, calls, access_role="writer")

    code = rights.main(["--json"])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "not open" not in out
    rows = {row["key"]: row for row in json.loads(out)}
    live = rows[google_targets.LIVE_CALENDAR]
    assert live["ok"] is True and live["role"] == "writer", live
    assert any(rights.ACL_PROBE in warning for warning in live["warnings"]), live
    assert "cal-live" not in events.inserted_calendars, "в боевой календарь ходили пробой"


def test_скрипты_ставят_корень_проекта_в_sys_path():
    """Точка входа `python scripts/<имя>.py` не зависит от PYTHONPATH (AC-5, AC-13)."""
    import google_seed_bindings as seed

    assert str(rights.ROOT) in sys.path
    assert str(seed.ROOT) in sys.path

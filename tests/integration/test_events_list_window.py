"""Окно и листание списка событий Mini App (docs/development/190 §4, AC-9).

Обоснование: FR-D3, `docs/development/190` §4 (таблица параметров), AC-9.
Список чата (`/tools/calendar/events`) не меняется: у Mini App свой адрес, потому
что ему нужно окно назад, страницы и `plan_id` строки. Проверяются обе страницы,
сумма элементов и отказы: окно больше 92 дней, `limit > 100`, чужой токен страницы.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn, google_error
from tests.integration.polls_helpers import token

ADMIN_CHAT = 123456789

EVENTS = [
    {"id": "ev-1", "summary": "[С] Жизнь насекомых", "colorId": "11",
     "status": "confirmed", "start": {"dateTime": "2026-11-24T19:00:00+04:00"},
     "end": {"dateTime": "2026-11-24T21:30:00+04:00"},
     "extendedProperties": {"private": {"svoi_event_id": "412", "svoi_kind": "show"}}},
    {"id": "ev-2", "summary": "Запись в Google", "status": "confirmed",
     "start": {"dateTime": "2026-11-25T10:00:00+04:00"},
     "end": {"dateTime": "2026-11-25T12:00:00+04:00"},
     "extendedProperties": {"private": {}}},
    {"id": "ev-3", "summary": "[Р] Чёрная пурга", "colorId": "9",
     "status": "confirmed", "start": {"date": "2026-11-26"},
     "end": {"date": "2026-11-27"},
     "extendedProperties": {"private": {"svoi_kind": "rehearsal"}}},
]


class FakeRequest:
    def __init__(self, answer=None, error=None):
        self.answer = answer or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.answer


class FakeEvents:
    """Календарь на две страницы: по 2 и 1 событию, как у Google с `nextPageToken`."""

    def __init__(self) -> None:
        self.params: list[dict] = []

    def list(self, **params):
        self.params.append(params)
        if params.get("pageToken"):
            return FakeRequest({"items": EVENTS[2:]})
        return FakeRequest({"items": EVENTS[:2], "nextPageToken": "page-2"})


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self) -> FakeEvents:
        return self._events


def _patch(monkeypatch, error=None):
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-cal")
    events = FakeEvents()
    if error is not None:
        events.list = lambda **_params: FakeRequest(error=error)
    monkeypatch.setattr(core, "_calendar", lambda: FakeService(events))
    return events


def _plan() -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, status,
                                       gcal_event_id, created_by)
               values ('Жизнь насекомых', 'show', '2026-11-24T19:00:00+04',
                       '2026-11-24T21:30:00+04', 'confirmed', 'ev-1', null)
               returning id""").fetchone()[0]


def test_месяц_листается_страницами(client, monkeypatch) -> None:
    """AC-9: `next_page_token` и вторая страница; сумма — все события окна."""
    _patch(monkeypatch)
    headers = token(client, ADMIN_CHAT)
    first = client.get("/webapp/events/list?calendar=read"
                       "&from=2026-11-01&to=2026-11-30", headers=headers).json()
    assert first["count"] == 2 and first["next_page_token"] == "page-2"
    second = client.get("/webapp/events/list?calendar=read"
                        "&from=2026-11-01&to=2026-11-30&page_token=page-2",
                        headers=headers).json()
    assert second["count"] == 1 and second["next_page_token"] is None
    assert first["count"] + second["count"] == len(EVENTS)


def test_строка_плана_и_тип_по_метке(client, monkeypatch) -> None:
    """§4: `has_plan`/`plan_id` по маркеру, тип — по `svoi_kind` или метке."""
    _patch(monkeypatch)
    plan_id = _plan()
    headers = token(client, ADMIN_CHAT)
    items = client.get("/webapp/events/list?calendar=read&from=2026-11-01"
                       "&to=2026-11-30", headers=headers).json()["items"]
    by_id = {item["event_id"]: item for item in items}
    # подменённый клиент отдаёт маркер 412, а строки с таким id в базе нет: это «сирота»,
    # поэтому plan_id не подтверждён, но тип по `svoi_kind` всё равно назван
    assert by_id["ev-1"]["plan_id"] is None and by_id["ev-1"]["has_plan"] is False
    assert by_id["ev-1"]["letter"] == "С" and by_id["ev-1"]["kind"] == "show"
    assert by_id["ev-2"]["letter"] is None and by_id["ev-2"]["kind"] is None
    second = client.get("/webapp/events/list?calendar=read&from=2026-11-01"
                        "&to=2026-11-30&page_token=page-2", headers=headers).json()
    all_day = next(item for item in second["items"] if item["event_id"] == "ev-3")
    assert all_day["all_day"] is True and all_day["kind"] == "rehearsal"
    assert plan_id > 0


def test_окно_больше_девяноста_двух_дней_отказ(client, monkeypatch) -> None:
    """§4: `bad_window` — 400, а не чтение сезона одним запросом."""
    events = _patch(monkeypatch)
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/list?calendar=read&from=2026-09-01"
                        "&to=2026-12-31", headers=headers)
    assert answer.status_code == 400 and answer.json()["code"] == "bad_window"
    assert events.params == [], "окно отвергнуто, но Google уже читали"


def test_конец_раньше_начала_и_плохие_даты(client, monkeypatch) -> None:
    """§4: `to >= from` и формат дат — один код `bad_window`."""
    _patch(monkeypatch)
    headers = token(client, ADMIN_CHAT)
    backwards = client.get("/webapp/events/list?calendar=read&from=2026-11-30"
                           "&to=2026-11-01", headers=headers)
    assert backwards.status_code == 400 and backwards.json()["code"] == "bad_window"
    broken = client.get("/webapp/events/list?calendar=draft&from=ноябрь&to=2026-12-01",
                        headers=headers)
    assert broken.status_code == 400 and broken.json()["code"] == "bad_window"


def test_предел_страницы_и_токен(client, monkeypatch) -> None:
    """§4: `limit > 100` — 400 `bad_limit`; чужой токен страницы — `bad_page_token`."""
    _patch(monkeypatch)
    headers = token(client, ADMIN_CHAT)
    too_many = client.get("/webapp/events/list?calendar=read&from=2026-11-01"
                          "&to=2026-11-30&limit=101", headers=headers)
    assert too_many.status_code == 400 and too_many.json()["code"] == "bad_limit"
    zero = client.get("/webapp/events/list?calendar=read&from=2026-11-01"
                      "&to=2026-11-30&limit=0", headers=headers)
    assert zero.status_code == 400 and zero.json()["code"] == "bad_limit"

    _patch(monkeypatch, error=google_error(400))
    stale = client.get("/webapp/events/list?calendar=read&from=2026-11-01"
                       "&to=2026-11-30&page_token=old", headers=headers)
    assert stale.status_code == 400 and stale.json()["code"] == "bad_page_token"


def test_чужой_календарь_отказ_словами(client, monkeypatch) -> None:
    """§4: `calendar` — только `draft`/`read`; иначе 400 `bad_calendar`."""
    _patch(monkeypatch)
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/list?calendar=live&from=2026-11-01"
                        "&to=2026-11-30", headers=headers)
    assert answer.status_code == 400 and answer.json()["code"] == "bad_calendar"

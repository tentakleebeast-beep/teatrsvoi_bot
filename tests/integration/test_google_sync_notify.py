"""V-133: отмена и перенос, сделанные человеком в Google, доходят до участников.

Обоснование: `docs/reports/194` (анкета актёров, «не узнаю и приезжаю»),
`docs/development/186` §5 (сверка по маркеру `svoi_event_id`),
`docs/development/57` (коды `event_cancelled` и `event_moved`).

Проверяется на живой схеме, а не на заглушке: `notification` имеет уникальный
`(dedupe_key, recipient_id)`, и именно он не даёт второй рассылки при повторной сверке.
Заглушка Google отдаёт событие с маркером бота, поэтому сверка идёт тем же путём, что и
настоящая: `POST /tools/google/refresh` → `calendar_sync.scan` → `event_plan` → очередь.

Боевой календарь в тесте не пишется: события помечены `gcal_calendar_id = read`, значит
черновика нет и `events.delete` не вызывается.
"""

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn

TZ = timezone(timedelta(hours=4))


class FakeEvents:
    """Выдача календаря: одно событие, сколько бы страниц ни просили."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def list(self, **_params):
        return FakeRequest({"items": self.items})

    def delete(self, calendarId=None, eventId=None, **_kwargs):
        return FakeRequest({})


class FakeRequest:
    def __init__(self, answer: dict) -> None:
        self.answer = answer

    def execute(self) -> dict:
        return self.answer


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self) -> FakeEvents:
        return self._events


def _add_person(chat_id: int, name: str) -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (%s, %s, 'troupe', 'active') returning id""",
            (chat_id, name)).fetchone()
        conn.execute("insert into participant_capability_override "
                     "(participant_id, capability, allowed, reason) "
                     "values (%s, 'schedule.read.all', true, 'тест сверки') "
                     "on conflict do nothing", (row[0],))
    return row[0]


def _add_event(created_by: int, people: list[int],
               start: datetime) -> int:
    """Строка события бота, уже живущая в боевом календаре (`calendar = read`)."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, participant_ids,
                                     status, gcal_event_id, gcal_calendar_id, created_by)
               values ('Прогон сверки', 'rehearsal', %s, %s, %s, 'confirmed', %s, 'read', %s)
               returning id""",
            (start, start + timedelta(hours=3), people, "live-1", created_by)).fetchone()
    return row[0]


def _google_event(event_id: int, start: datetime, *, status: str = "confirmed") -> dict:
    return {"id": "live-1", "summary": "[Р] Прогон сверки", "status": status,
            "colorId": "9", "updated": start.isoformat(),
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=3)).isoformat()},
            "extendedProperties": {"private": {"svoi_event_id": str(event_id),
                                               "svoi_kind": "rehearsal"}}}


def _auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _refresh(client, monkeypatch, chat_id: int, items: list[dict],
             around: datetime) -> dict:
    """Позвать «Сверить» на заглушке Google: окно — день вокруг события."""
    from app import core as api

    monkeypatch.setattr(api, "CALENDAR_ID_READ", "live-calendar")
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(FakeEvents(items)))
    monkeypatch.setattr(api, "_calendar_write", lambda: FakeService(FakeEvents([])))
    body = client.post("/tools/google/refresh", headers=_auth(chat_id), json={
        "sources": ["calendar.read"],
        "window": {"from": (around - timedelta(days=1)).date().isoformat(),
                   "to": (around + timedelta(days=1)).date().isoformat()},
    }).json()
    assert "sources" in body, body
    return body["sources"]["calendar.read"]


def _notifications(dedupe_key: str) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select recipient_id, kind, payload from notification "
            "where dedupe_key = %s order by recipient_id", (dedupe_key,)).fetchall()


def _status(event_id: int) -> str:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute("select status from event_plan where id = %s",
                            (event_id,)).fetchone()[0]


def test_отмена_в_google_приходит_участникам_ровно_один_раз(client, monkeypatch):
    owner = _add_person(891001, "Организатор Сверки")
    actor = _add_person(891002, "Участник Сверки")
    start = (datetime.now(TZ) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    event_id = _add_event(owner, [owner, actor], start)
    items = [_google_event(event_id, start, status="cancelled")]

    first = _refresh(client, monkeypatch, 891001, items, start)

    assert first["notified"]["cancelled"] == 2, "участник и создатель"
    notes = _notifications(f"event:{event_id}:cancelled")
    assert {note["recipient_id"] for note in notes} == {owner, actor}
    assert {note["kind"] for note in notes} == {"event_cancelled"}
    assert all("отменяется" in note["payload"]["body"] for note in notes)
    assert _status(event_id) == "cancelled"

    second = _refresh(client, monkeypatch, 891001, items, start)

    assert second["notified"]["cancelled"] == 0, "второй сверки не рассылаем"
    assert len(_notifications(f"event:{event_id}:cancelled")) == 2


def test_перенос_в_google_приходит_с_прежним_и_новым_временем(client, monkeypatch):
    owner = _add_person(891101, "Организатор Переноса")
    actor = _add_person(891102, "Участник Переноса")
    start = (datetime.now(TZ) + timedelta(days=4)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    new_start = start.replace(hour=15)
    event_id = _add_event(owner, [owner, actor], start)
    items = [_google_event(event_id, new_start)]

    report = _refresh(client, monkeypatch, 891101, items, start)

    assert report["notified"]["moved"] == 2, "участник и создатель"
    notes = _notifications(f"event:{event_id}:moved:{new_start.isoformat()}")
    assert {note["kind"] for note in notes} == {"event_moved"}
    assert all(note["payload"]["was"] == start.isoformat() for note in notes)
    assert all(note["payload"]["now"] == new_start.isoformat() for note in notes)
    assert _status(event_id) == "confirmed", "правку времени в базу не переносим"

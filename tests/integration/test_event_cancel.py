"""FR-D18: отмена события — организатор своё, администратор любое (решение 27.10).

Проверяется: снятие из календаря, 'event_plan.status', уведомления участникам и
организатору, аудит, идемпотентность и права.
"""

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn

TZ = timezone(timedelta(hours=4))


class FakeEvents:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.deleted = []

    def delete(self, calendarId, eventId):
        self.deleted.append((calendarId, eventId))
        return self

    def execute(self):
        if self.fail:
            from tests.conftest import google_error

            raise google_error()
        return {}


class FakeCalendar:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


def _add_person(chat_id: int, name: str, role: str = "troupe") -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (%s, %s, %s, 'active') returning id""",
            (chat_id, name, role)).fetchone()
        conn.execute("insert into participant_capability_override "
                     "(participant_id, capability, allowed, reason) "
                     "values (%s, 'schedule.write', true, 'тест отмены') "
                     "on conflict do nothing", (row[0],))
        conn.execute("insert into participant_role (participant_id, role) values (%s, %s) "
                     "on conflict do nothing", (row[0], role))
    return row[0]


def _auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _add_event(created_by: int, people: list[int], gcal: str | None = "gcal-1",
               status: str = "confirmed") -> int:
    start = datetime.now(TZ).replace(microsecond=0) + timedelta(days=3)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, participant_ids,
                                     status, gcal_event_id, created_by)
               values ('Прогон', 'rehearsal', %s, %s, %s, %s, %s, %s) returning id""",
            (start, start + timedelta(hours=3), people, status, gcal,
             created_by)).fetchone()
    return row[0]


def _event_row(event_id: int) -> dict:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("select status from event_plan where id = %s",
                            (event_id,)).fetchone()


def _notifications(event_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select recipient_id, kind, payload from notification "
            "where dedupe_key = %s order by recipient_id",
            (f"event:{event_id}:cancelled",)).fetchall()


def _audit(event_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select action, before, after from audit_event "
            "where entity_type = 'event_plan' and entity_id = %s",
            (str(event_id),)).fetchall()


def test_организатор_отменяет_своё_событие(client, monkeypatch):
    from app import core as api

    owner = _add_person(882001, "Организатор Свой")
    actor = _add_person(882002, "Участник Первый")
    event_id = _add_event(owner, [owner, actor])
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    body = client.post("/tools/events/cancel", headers=_auth(882001),
                       json={"event_id": event_id, "reason": "Заболел педагог"}).json()

    assert body["ok"] is True
    assert body["status"] == "cancelled"
    assert body["in_calendar"] is True
    assert body["notified"] == 2, "организатор и участник"
    assert fake.deleted == [("draft-calendar", "gcal-1")]
    assert _event_row(event_id)["status"] == "cancelled"

    notes = _notifications(event_id)
    assert {note["recipient_id"] for note in notes} == {owner, actor}
    assert all(note["kind"] == "event_cancelled" for note in notes)
    text = notes[0]["payload"]["body"]
    assert "отменяется" in text and "Заболел педагог" in text

    recorded = _audit(event_id)
    assert len(recorded) == 1
    assert recorded[0]["action"] == "event.cancel"
    assert recorded[0]["before"]["status"] == "confirmed"
    assert recorded[0]["after"]["status"] == "cancelled"


def test_админ_отменяет_чужое_событие(client, monkeypatch):
    from app import core as api

    owner = _add_person(882101, "Организатор Чужой")
    event_id = _add_event(owner, [owner])
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    _add_person(882103, "Админ Отменяющий", role="admin")
    body = client.post("/tools/events/cancel", headers=_auth(882103),
                       json={"event_id": event_id}).json()

    assert body["ok"] is True
    assert fake.deleted == [("read-calendar", "gcal-1")], "чернового нет — снимаем из боевого"
    assert _event_row(event_id)["status"] == "cancelled"


def test_чужое_событие_отменить_нельзя(client):
    owner = _add_person(882201, "Организатор Занятый")
    _add_person(882202, "Чужой Участник")
    event_id = _add_event(owner, [owner])

    response = client.post("/tools/events/cancel", headers=_auth(882202),
                           json={"event_id": event_id})

    assert response.status_code == 403
    assert _event_row(event_id)["status"] == "confirmed"


def test_повторная_отмена_идемпотентна(client):
    owner = _add_person(882301, "Организатор Повтор")
    event_id = _add_event(owner, [owner], status="cancelled")

    body = client.post("/tools/events/cancel", headers=_auth(882301),
                       json={"event_id": event_id}).json()

    assert body["ok"] is True
    assert body["already_cancelled"] is True
    assert body["notified"] == 0
    assert _notifications(event_id) == []


def test_недоступный_календарь_не_отменяет_отмену(client, monkeypatch):
    from app import core as api

    owner = _add_person(882401, "Организатор Гугл")
    event_id = _add_event(owner, [owner])
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(FakeEvents(fail=True)))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    body = client.post("/tools/events/cancel", headers=_auth(882401),
                       json={"event_id": event_id}).json()

    assert body["ok"] is True
    assert body["in_calendar"] is False
    assert "backendError" in body["calendar_error"]
    assert _event_row(event_id)["status"] == "cancelled"


def test_несуществующее_событие(client):
    _add_person(882501, "Организатор Пустой")
    response = client.post("/tools/events/cancel", headers=_auth(882501),
                           json={"event_id": 999999})
    assert response.status_code == 404


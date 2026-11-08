"""FR-D19: перенос события — организатор своё, администратор любое (решение 27.10).

Проверяется: сдвиг времени в БД и календаре, сохранение длительности, повторная
проверка занятости, уведомление «было → стало», аудит и права.
"""

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn

TZ = timezone(timedelta(hours=4))


class FakeEvents:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.updated = []

    def update(self, calendarId, eventId, body):
        self.updated.append((calendarId, eventId, body))
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
                     "values (%s, 'schedule.write', true, 'тест переноса') "
                     "on conflict do nothing", (row[0],))
    return row[0]


def _auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _add_event(created_by: int, people: list[int], gcal: str | None = "gcal-1",
               status: str = "confirmed") -> int:
    start = datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0) \
        + timedelta(days=3)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, participant_ids,
                                     status, gcal_event_id, created_by, location)
               values ('Прогон', 'rehearsal', %s, %s, %s, %s, %s, %s, 'test-hall')
               returning id""",
            (start, start + timedelta(hours=2), people, status, gcal,
             created_by)).fetchone()
    return row[0]


def _event_row(event_id: int) -> dict:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("select starts_at, ends_at, status from event_plan "
                            "where id = %s", (event_id,)).fetchone()


def _notifications(event_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select recipient_id, kind, payload, dedupe_key from notification "
            "where dedupe_key like %s order by recipient_id",
            (f"event:{event_id}:moved:%",)).fetchall()


def _audit(event_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select action, before, after from audit_event "
            "where entity_type = 'event_plan' and entity_id = %s and action = 'event.move'",
            (str(event_id),)).fetchall()


def test_организатор_переносит_своё_событие(client, monkeypatch):
    from app import core as api

    owner = _add_person(883001, "Организатор Перенос")
    actor = _add_person(883002, "Участник Переноса")
    event_id = _add_event(owner, [owner, actor])
    old = _event_row(event_id)
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    new_date = (old["starts_at"].astimezone(TZ) + timedelta(days=5)).date().isoformat()
    body = client.post("/tools/events/move", headers=_auth(883001),
                       json={"event_id": event_id, "date": new_date, "from_hour": 18,
                             "reason": "Педагог просил позже"}).json()

    assert body["ok"] is True and body["changed"] is True
    assert body["in_calendar"] is True
    assert body["notified"] == 2, "организатор и участник"
    assert body["busy_people"] == []

    row = _event_row(event_id)
    new_start = row["starts_at"].astimezone(TZ)
    assert new_start.date().isoformat() == new_date
    assert (new_start.hour, new_start.minute) == (18, 0)
    assert row["ends_at"] - row["starts_at"] == timedelta(hours=2), "длительность сохранена"

    assert fake.updated[0][0] == "draft-calendar"
    assert fake.updated[0][1] == "gcal-1"
    assert fake.updated[0][2]["start"]["dateTime"].startswith(new_date)

    notes = _notifications(event_id)
    assert {note["recipient_id"] for note in notes} == {owner, actor}
    assert all(note["kind"] == "event_moved" for note in notes)
    text = notes[0]["payload"]["body"]
    assert "переносится" in text and "было" in text and "Педагог просил позже" in text

    recorded = _audit(event_id)
    assert len(recorded) == 1
    assert recorded[0]["before"]["starts_at"] != recorded[0]["after"]["starts_at"]
    assert recorded[0]["after"]["calendar_updated"] is True


def test_админ_переносит_чужое_событие(client, monkeypatch):
    from app import core as api

    owner = _add_person(883101, "Организатор Чужой Перенос")
    event_id = _add_event(owner, [owner])
    old = _event_row(event_id)
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    _add_person(883103, "Админ Переносящий", role="admin")
    new_date = (old["starts_at"].astimezone(TZ) + timedelta(days=1)).date().isoformat()
    body = client.post("/tools/events/move", headers=_auth(883103),
                       json={"event_id": event_id, "date": new_date}).json()

    assert body["ok"] is True
    assert fake.updated[0][0] == "read-calendar", "чернового нет — правим боевой"
    assert _event_row(event_id)["starts_at"].astimezone(TZ).date().isoformat() == new_date


def test_чужое_событие_перенести_нельзя(client):
    owner = _add_person(883201, "Организатор Занят")
    _add_person(883202, "Чужой Переносящий")
    event_id = _add_event(owner, [owner])
    old = _event_row(event_id)

    response = client.post("/tools/events/move", headers=_auth(883202),
                           json={"event_id": event_id, "from_hour": 20})

    assert response.status_code == 403
    assert _event_row(event_id)["starts_at"] == old["starts_at"]


def test_отменённое_событие_не_переносится(client):
    owner = _add_person(883301, "Организатор Отмены")
    event_id = _add_event(owner, [owner], status="cancelled")

    response = client.post("/tools/events/move", headers=_auth(883301),
                           json={"event_id": event_id, "from_hour": 20})

    assert response.status_code == 409


def test_недоступный_календарь_не_мешает_переносу(client, monkeypatch):
    from app import core as api

    owner = _add_person(883401, "Организатор Гугл Перенос")
    event_id = _add_event(owner, [owner])
    monkeypatch.setattr(api, "_calendar", lambda: FakeCalendar(FakeEvents(fail=True)))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")

    body = client.post("/tools/events/move", headers=_auth(883401),
                       json={"event_id": event_id, "from_hour": 19}).json()

    assert body["ok"] is True
    assert body["in_calendar"] is False
    assert "backendError" in body["calendar_error"]
    assert _event_row(event_id)["starts_at"].astimezone(TZ).hour == 19


def test_перенос_за_рабочий_день_отклоняется(client):
    owner = _add_person(883501, "Организатор Ночь")
    event_id = _add_event(owner, [owner])
    old = _event_row(event_id)

    response = client.post("/tools/events/move", headers=_auth(883501),
                           json={"event_id": event_id, "from_hour": 2})

    assert response.status_code == 400
    assert _event_row(event_id)["starts_at"] == old["starts_at"]


def test_перенос_на_то_же_время_ничего_не_меняет(client):
    owner = _add_person(883601, "Организатор Тот Же")
    event_id = _add_event(owner, [owner])

    body = client.post("/tools/events/move", headers=_auth(883601),
                       json={"event_id": event_id}).json()

    assert body["ok"] is True and body["changed"] is False
    assert body["notified"] == 0
    assert _notifications(event_id) == []


def test_несуществующее_событие(client):
    _add_person(883701, "Организатор Пустой Перенос")
    response = client.post("/tools/events/move", headers=_auth(883701),
                           json={"event_id": 999999})
    assert response.status_code == 404

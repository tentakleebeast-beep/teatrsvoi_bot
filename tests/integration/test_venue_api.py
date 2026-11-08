"""Аренда: свободные окна зала через freebusy (docs/architecture/46)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


class FakeFreebusy:
    def __init__(self, busy):
        self._busy = busy
        self.called = False

    def query(self, body):
        self.called = True
        self.body = body
        return self

    def execute(self):
        return {"calendars": {"venue-1": {"busy": self._busy}}}


class FakeService:
    def __init__(self, freebusy):
        self._freebusy = freebusy

    def freebusy(self):
        return self._freebusy


def _allow(client, capability="calendar.write"):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 123456789").fetchone()[0]
        conn.execute("""insert into participant_capability_override
                          (participant_id, capability, allowed, reason)
                        values (%s, %s, true, 'тест зала')
                        on conflict (participant_id, capability) do update set allowed = true""",
                     (pid, capability))
        conn.commit()


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_аренда_без_персонала_это_окна_зала(client, monkeypatch):
    from app import core as api

    events = [{
        "id": "ev-1", "summary": "Аренда - Валерий",
        "start": {"dateTime": "2026-12-22T12:00:00+04:00"},
        "end": {"dateTime": "2026-12-22T16:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_venue": "stage"}},
    }]
    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: events)

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "venue": "stage", "date_from": "2026-12-22",
        "date_to": "2026-12-22", "min_hours": 2, "block_events": False}).json()

    assert body["mode"] == "venue"
    assert body["venue"]["applied"] is True
    assert body["venue"]["busy"] == 1
    spans = [(w["from_hour"], w["to_hour"]) for w in body["windows"]]
    assert (8, 12) in spans and (16, 24) in spans
    assert all(not (start <= 12 < end or start <= 15 < end) for start, end in spans)


def test_аренда_с_персоналом_учитывает_и_зал_и_людей(client, monkeypatch):
    from app import core as api

    events = [{
        "id": "ev-2", "summary": "Показ",
        "start": {"dateTime": "2026-12-23T18:00:00+04:00"},
        "end": {"dateTime": "2026-12-23T22:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_venue": "stage"}},
    }]
    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: events)

    with psycopg.connect(_test_dsn()) as conn:
        staff = _person(conn, 792001, "Админ Зал")
        for hour in range(8, 23):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s,'2026-12-23',%s,true,'declared','bot')""",
                         (staff, hour))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "venue": "stage", "participants": [staff],
        "date_from": "2026-12-23", "date_to": "2026-12-23", "min_hours": 2,
        "block_events": False}).json()

    assert body["policy"] == "rental_staff"
    for window in body["windows"]:
        assert window["to_hour"] <= 18 or window["from_hour"] >= 22, \
            f"окно {window} попадает на занятый зал"


def test_без_freebusy_аренда_работает_как_раньше(client, monkeypatch):
    from app import core as api

    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: [])

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "date_from": "2026-12-24", "date_to": "2026-12-24",
        "min_hours": 2, "block_events": False}).json()
    assert body["venue"]["applied"] is False

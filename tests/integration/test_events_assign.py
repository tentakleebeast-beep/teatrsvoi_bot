"""Назначение события: предпросмотр, проверки, запись, уведомления (docs/architecture/50-51)."""

import json
import zoneinfo

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


class FakeEvents:
    def __init__(self):
        self.sent = None

    def insert(self, calendarId, body):
        self.sent = body
        return self

    def execute(self):
        return {"id": "gcal-1"}


class FakeService:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


class FakeBusy:
    def __init__(self, busy):
        self._busy = busy

    def query(self, body):
        return self

    def execute(self):
        return {"calendars": {"stage": {"busy": self._busy}}}


def _allow(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 123456789").fetchone()[0]
        for capability in ("schedule.write", "calendar.write"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тест назначения')
                            on conflict (participant_id, capability) do update set allowed = true""",
                         (pid, capability))
        conn.commit()


def _person(conn, chat_id: int, name: str, short: str | None = None) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    if short:
        conn.execute("insert into person_alias (participant_id, alias, alias_kind) "
                     "values (%s,%s,'short')", (pid, short))
    conn.commit()
    return pid


HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def test_предпросмотр_по_тексту(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(FakeEvents()))
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 783001, "Новиков Виктор", "Витя")
        luda = _person(conn, 783002, "Волкова Людмила", "Люда")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True,
        "text": "назначь репетицию с Витей и Людой в четверг в 20:00"}).json()

    assert body["ok"] is True, body
    card = body["card"]
    assert card["kind"] == "rehearsal"
    assert card["from_hour"] == 20 and card["to_hour"] == 22
    ids = {person["id"] for person in card["participants"]}
    assert ids == {vitya, luda}, "имена актёров важнее ролей"
    assert body["proposal_id"]


def test_диалог_уточнений_собирает_встречу(client, monkeypatch):
    """Живой диалог 05.11.2026: «встреча с Марком и Машей» → «завтра» → «с 15-00 до 18-00».

    Gateway дописывает каждую реплику к той же фразе (карта t_ebce081e), поэтому сервер
    должен отвечать ровно тем, чего не хватает: сначала день, потом время, потом карточка.
    """
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        mark = _person(conn, 783020, "Диалог Марк", "Марк")
        masha = _person(conn, 783021, "Диалог Маша", "Маша")

    def preview(text: str) -> dict:
        return client.post("/tools/events/preview", headers=HEADERS,
                           json={"text": text, "persist": True}).json()

    said = "поставь встречу с Марком и Машей"
    first = preview(said)
    assert [error["code"] for error in first["errors"]] == ["no_date"], first

    said = f"{said} завтра"
    second = preview(said)
    assert [error["code"] for error in second["errors"]] == ["no_time"], second

    said = f"{said} с 15-00 до 18-00"
    third = preview(said)
    assert third["ok"] is True, third
    assert (third["card"]["from_hour"], third["card"]["to_hour"]) == (15, 18)
    assert {person["id"] for person in third["card"]["participants"]} == {mark, masha}


def test_занятость_людей_предупреждает_но_не_блокирует(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 783003, "Новиков Виктор", "Витя")
        for hour in (20, 21):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s, '2026-12-17', %s, false, 'busy', 'bot')""",
                         (vitya, hour))
        conn.commit()

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-17", "from_hour": 20, "to_hour": 22,
        "participants": [vitya]}).json()

    assert body["ok"] is True
    assert any(w["code"] == "participant_busy" for w in body["warnings"])
    assert body["can_assign"] is True, "занятость людей — предупреждение, решает человек"


def test_занятое_помещение_блокирует(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app import core as api

    _allow(client)
    tz = timezone(timedelta(hours=4))
    busy = [api.Busy(datetime(2026, 12, 18, 19, 0, tzinfo=tz),
                     datetime(2026, 12, 18, 22, 0, tzinfo=tz))] if hasattr(api, "Busy") else []
    if not busy:
        from app.venue import Busy
        busy = [Busy(datetime(2026, 12, 18, 19, 0, tzinfo=tz),
                     datetime(2026, 12, 18, 22, 0, tzinfo=tz))]
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: (busy, {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 783004, "Назначение Помещение")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "show", "date": "2026-12-18", "from_hour": 20, "to_hour": 22,
        "participants": [pid]}).json()

    assert body["ok"] is False
    assert any(e["code"] == "venue_busy" for e in body["errors"])
    assert body["can_assign"] is False


def test_назначение_создаёт_запись_и_уведомления(client, monkeypatch):
    from app import core as api

    _allow(client)
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))

    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 783005, "Новиков Виктор", "Витя")
        luda = _person(conn, 783006, "Волкова Людмила", "Люда")

    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True,
        "kind": "rehearsal", "title": "Жизнь насекомых", "date": "2026-12-19",
        "from_hour": 18, "to_hour": 21, "participants": [vitya, luda]}).json()
    assert preview["ok"] is True

    assigned = client.post("/tools/events/assign", headers=HEADERS,
                           json={"proposal_id": preview["proposal_id"]}).json()
    assert assigned["ok"] is True
    assert assigned["gcal_event_id"] == "gcal-1"
    assert assigned["notified"] == 2

    private = fake.sent["extendedProperties"]["private"]
    assert private["svoi_kind"] == "rehearsal"
    assert private["svoi_participant_ids"] == f"{vitya},{luda}"

    with psycopg.connect(_test_dsn()) as conn:
        plan = conn.execute("select status, participant_ids, gcal_event_id from event_plan "
                            "where id = %s", (assigned["event_id"],)).fetchone()
        notes = conn.execute("select recipient_id from notification "
                             "where kind = 'event_assigned'").fetchall()
    assert plan[0] == "confirmed" and plan[2] == "gcal-1"
    assert {row[0] for row in notes} == {vitya, luda}


def test_назначение_пишет_машинные_свойства(client, monkeypatch):
    """Карточка → календарь: свойства, по которым поиск окон понимает занятость.

    Перенесено из `test_calendar_event_create.py`: маршрут `POST /tools/calendar/event`
    удалён (дубль С1), а проверяемое им свойство обязано жить на единственном пути.
    """
    from app import core as api

    _allow(client)
    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(fake))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 783010, "Показ Виктор")
        luda = _person(conn, 783011, "Показ Людмила")
        project_id = conn.execute("insert into project (name) values ('Жизнь насекомых') "
                                  "returning id").fetchone()[0]
        conn.commit()

    body = client.post("/tools/events/assign", headers=HEADERS, json={"card": {
        "kind": "show", "title": "Жизнь насекомых", "date": "2026-12-20",
        "from_hour": 19, "to_hour": 22, "project_id": project_id,
        "participants": [{"id": vitya, "name": "Показ Виктор"},
                         {"id": luda, "name": "Показ Людмила"}]}}).json()

    assert body["ok"] is True
    assert fake.sent["summary"] == "Показ - Жизнь насекомых"
    assert fake.sent["colorId"] == "11", "показ — красный по палитре"
    private = fake.sent["extendedProperties"]["private"]
    assert private["svoi_kind"] == "show"
    assert private["svoi_participant_ids"] == f"{vitya},{luda}"
    assert private["svoi_project_id"] == str(project_id)


def test_конец_дня_обрезается_до_23_55(client, monkeypatch):
    """Событие «до 24:00» заканчивается 23:55, а не уходит в следующие сутки."""
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(FakeEvents()))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 783012, "Ночной Прогон")

    body = client.post("/tools/events/assign", headers=HEADERS, json={"card": {
        "kind": "rehearsal", "title": "Ночной прогон", "date": "2026-12-21",
        "from_hour": 22, "to_hour": 24, "participants": [{"id": pid}]}}).json()
    assert body["ok"] is True

    with psycopg.connect(_test_dsn()) as conn:
        ends_at = conn.execute("select ends_at from event_plan where id = %s",
                               (body["event_id"],)).fetchone()[0]
    assert ends_at.astimezone(zoneinfo.ZoneInfo("Europe/Samara")).strftime("%H:%M") == "23:55"


def test_неизвестный_тип_отклоняется(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))

    response = client.post("/tools/events/assign", headers=HEADERS, json={"card": {
        "kind": "концерт", "title": "Что-то", "date": "2026-12-22",
        "from_hour": 18, "to_hour": 20}})
    assert response.status_code == 400
    assert response.json()["detail"] == "unknown_kind"


def test_без_права_назначить_нельзя(client):
    response = client.post("/tools/events/assign", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "999999"},
        json={"card": {"kind": "show", "title": "Тест", "date": "2026-12-23",
                       "from_hour": 18, "to_hour": 20}})
    assert response.status_code in (401, 403)


def test_повторное_назначение_отклоняется(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(FakeEvents()))
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 783007, "Назначение Дубль")

    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True,
        "kind": "training", "date": "2026-12-20", "from_hour": 18, "to_hour": 20,
        "participants": [pid]}).json()
    client.post("/tools/events/assign", headers=HEADERS,
                json={"proposal_id": preview["proposal_id"]})

    again = client.post("/tools/events/assign", headers=HEADERS,
                        json={"proposal_id": preview["proposal_id"]})
    assert again.status_code == 400

"""Неоднозначные имена и правила состава: педагоги, режиссёр (docs/architecture/52)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


class FakeEvents:
    def insert(self, calendarId, body):
        return self

    def execute(self):
        return {"id": "gcal-x"}


class FakeService:
    def events(self):
        return FakeEvents()


def _allow(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 123456789").fetchone()[0]
        for capability in ("schedule.write", "calendar.write"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тест неоднозначности')
                            on conflict (participant_id, capability) do update set allowed = true""",
                         (pid, capability))
        conn.commit()


def _person(conn, chat_id: int, name: str, short: str | None = None,
            teacher: bool = False, director: bool = False) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status, "
        "is_teacher, is_director, specialty) "
        "values (%s,%s,%s,'troupe','active',%s,%s,%s) returning id",
        (chat_id, name, make_key(name), teacher, director,
         "музыка" if teacher else None)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    if short:
        conn.execute("insert into person_alias (participant_id, alias, alias_kind) "
                     "values (%s,%s,'short')", (pid, short))
    conn.commit()
    return pid


def _setup_calendar(monkeypatch, api):
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))


def test_три_вити_дают_выбор(client, monkeypatch):
    from app import core as api

    _allow(client)
    _setup_calendar(monkeypatch, api)
    with psycopg.connect(_test_dsn()) as conn:
        first = _person(conn, 784001, "Иванов Виктор", "Витя")
        second = _person(conn, 784002, "Новиков Виктор", "Витя")
        third = _person(conn, 784003, "Крылов Виктор", "Витя")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True, "kind": "rehearsal", "date": "2026-12-21", "from_hour": 18, "to_hour": 20,
        "text": "назначь репетицию с Витей в понедельник в 18:00"}).json()

    assert body["ok"] is False
    assert any(e["code"] == "ambiguous_names" for e in body["errors"])
    options = body["card"]["ambiguity"]["витей"]
    assert {item["id"] for item in options} == {first, second, third}, "форма покажет чекбоксы"


def test_выбор_снимает_неоднозначность(client, monkeypatch):
    from app import core as api

    _allow(client)
    _setup_calendar(monkeypatch, api)
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 784004, "Первый Витя", "Витя")
        chosen = _person(conn, 784005, "Второй Витя", "Витя")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True, "kind": "rehearsal", "date": "2026-12-22", "from_hour": 18, "to_hour": 20,
        "text": "назначь репетицию с Витей в вторник в 18:00",
        "chosen": {"витей": chosen}}).json()

    assert body["ok"] is True, body
    assert [p["id"] for p in body["card"]["participants"]] == [chosen]


def test_педагог_обязателен_на_тренинге(client, monkeypatch):
    from app import core as api

    _allow(client)
    _setup_calendar(monkeypatch, api)
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 784006, "Актёр Тренинг")
        teacher = _person(conn, 784007, "Педагог Музыка", teacher=True)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "date": "2026-12-23", "from_hour": 19, "to_hour": 21,
        "participants": [actor]}).json()

    ids = {person["id"] for person in body["card"]["participants"]}
    assert teacher in ids, "педагог входит в состав тренинга всегда"
    assert any(item["id"] == teacher for item in body["card"]["staff_required"])


def test_режиссёр_на_репетиции_необязателен(client, monkeypatch):
    from app import core as api

    _allow(client)
    _setup_calendar(monkeypatch, api)
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 784008, "Актёр Репетиция")
        director = _person(conn, 784009, "Режиссёр Тест", director=True)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-24", "from_hour": 18, "to_hour": 20,
        "participants": [actor]}).json()

    ids = {person["id"] for person in body["card"]["participants"]}
    directors = {item["id"]: item for item in body["card"]["directors"]}
    assert director not in ids, "режиссёр не входит в состав автоматически"
    assert directors[director]["will_attend"] is False, "присутствие фиксируется как факт"


def test_на_показ_режиссёр_тоже_предлагается(client, monkeypatch):
    from app import core as api

    _allow(client)
    _setup_calendar(monkeypatch, api)
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 784010, "Актёр Показ")
        director = _person(conn, 784011, "Режиссёр Показ", director=True)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "show", "date": "2026-12-25", "from_hour": 19, "to_hour": 22,
        "participants": [actor]}).json()
    row = next(item for item in body["card"]["directors"] if item["id"] == director)
    assert row["will_attend"] is False

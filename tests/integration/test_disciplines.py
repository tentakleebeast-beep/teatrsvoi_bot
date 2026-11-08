"""Дисциплины педагогов влияют на состав тренинга (docs/development/53)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


class FakeEvents:
    def insert(self, calendarId, body):
        return self

    def execute(self):
        return {"id": "gcal-d"}


class FakeService:
    def events(self):
        return FakeEvents()


def _allow(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 123456789").fetchone()[0]
        for capability in ("schedule.write", "calendar.write"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тест дисциплин')
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


def _teach(conn, pid: int, discipline: str, relation: str = "teacher") -> None:
    conn.execute("""insert into participant_discipline (participant_id, discipline_id, relation)
                    select %s, id, %s from discipline where code = %s
                    on conflict (participant_id, discipline_id)
                    do update set relation = excluded.relation""", (pid, relation, discipline))
    conn.commit()


def test_на_музыку_идёт_только_педагог_музыки(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))

    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 786001, "Актёр Музыка")
        music = _person(conn, 786002, "Педагог Музыки")
        acting = _person(conn, 786003, "Педагог Актёрского")
        _teach(conn, music, "music")
        _teach(conn, acting, "acting")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "discipline": "music", "date": "2026-12-28",
        "from_hour": 18, "to_hour": 20, "participants": [actor]}).json()

    ids = {person["id"] for person in body["card"]["participants"]}
    assert music in ids
    assert acting not in ids, "педагог другой дисциплины не нужен"


def test_на_актёрский_идёт_педагог_актёрского(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 786004, "Актёр Актёрский")
        music = _person(conn, 786005, "Педагог Музыки Два")
        acting = _person(conn, 786006, "Педагог Актёрского Два")
        _teach(conn, music, "music")
        _teach(conn, acting, "acting")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "discipline": "acting", "date": "2026-12-29",
        "from_hour": 18, "to_hour": 20, "participants": [actor]}).json()

    ids = {person["id"] for person in body["card"]["participants"]}
    assert acting in ids and music not in ids


def test_без_дисциплины_добавляются_все_с_предупреждением(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 786007, "Актёр Без Дисциплины")
        _teach(conn, _person(conn, 786008, "Педагог Один"), "music")
        _teach(conn, _person(conn, 786009, "Педагог Два"), "acting")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "date": "2026-12-30", "from_hour": 18, "to_hour": 20,
        "participants": [actor]}).json()
    assert any(w["code"] == "discipline_missing" for w in body["warnings"])


def test_режиссёр_фиксируется_как_факт_присутствия(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 786010, "Актёр Репетиция Два")
        director = _person(conn, 786011, "Режиссёр Факт")
        conn.execute("update participant set is_director = true where id = %s", (director,))
        conn.commit()

    without = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-31", "from_hour": 18, "to_hour": 20,
        "participants": [actor]}).json()
    director_row = next(d for d in without["card"]["directors"] if d["id"] == director)
    assert director_row["will_attend"] is False

    with_director = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-31", "from_hour": 18, "to_hour": 20,
        "participants": [actor, director]}).json()
    director_row = next(d for d in with_director["card"]["directors"] if d["id"] == director)
    assert director_row["will_attend"] is True


def test_справочник_дисциплин(client):
    body = client.get("/tools/disciplines", headers=HEADERS).json()
    codes = [item["code"] for item in body["disciplines"]]
    assert "acting" in codes and "music" in codes

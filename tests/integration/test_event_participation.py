"""Личное время участия в событии (V-134, docs/reports/194 §2 п.3).

Обоснование: docs/reports/194, решение владельца V-134 от 23.11.2026.

Проверяется то, что человек увидит, а не форма запроса: пустое окно значит весь
интервал события; заданное окно приходит своему участнику; чужое окно роли без
`schedule.write` не приходит; окно вне события — 422; перенос события личные
окна не сдвигает и говорит об этом предупреждением.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

ADMIN_CHAT = 123456789
HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(ADMIN_CHAT)}


class FakeEvents:
    def __init__(self):
        self.sent = None

    def insert(self, calendarId, body):
        self.sent = body
        return self

    def execute(self):
        return {"id": "gcal-v134"}


class FakeService:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


def _allow(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()[0]
        for capability in ("schedule.write", "calendar.write"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тест личного времени')
                            on conflict (participant_id, capability)
                            do update set allowed = true""", (pid, capability))
        conn.commit()


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _headers(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _prepare(monkeypatch):
    from app import core as api

    fake = FakeEvents()
    monkeypatch.setattr(api, "_calendar", lambda: FakeService(fake))
    monkeypatch.setattr(api, "_calendar_write", lambda: FakeService(fake))
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    return fake


def _assign(client, day: str, from_hour: int, to_hour: int, people: list[int],
            windows: list[dict] | None = None) -> dict:
    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True, "kind": "rehearsal", "title": "Репетиция V-134",
        "date": day, "from_hour": from_hour, "to_hour": to_hour,
        "participants": people, "windows": windows or []}).json()
    assert preview["ok"] is True, preview
    return client.post("/tools/events/assign", headers=HEADERS,
                       json={"proposal_id": preview["proposal_id"]}).json()


def test_пустое_окно_значит_весь_интервал_события(client, monkeypatch):
    """Без «с / до» карточка показывает интервал события, а не пустоту."""
    _allow(client)
    _prepare(monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 785001, "Пустое Окно Виктор")

    assigned = _assign(client, "2026-12-21", 19, 21, [vitya])
    assert assigned["ok"] is True, assigned

    card = client.get(f"/tools/events/{assigned['event_id']}",
                      headers=_headers(785001)).json()["event"]
    assert card["my_time"]["time_from"] == "19:00"
    assert card["my_time"]["time_to"] == "21:00"
    assert card["participation"][0]["time_from"] == "19:00"
    assert card["participation"][0]["time_to"] == "21:00"


def test_заданное_окно_отдаётся_своему_участнику(client, monkeypatch):
    """Актёр без `schedule.write` видит своё окно и только свою строку."""
    _allow(client)
    _prepare(monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 785002, "Своё Окно Виктор")
        luda = _person(conn, 785003, "Своё Окно Людмила")

    assigned = _assign(client, "2026-12-22", 18, 22, [vitya, luda],
                       windows=[{"participant_id": vitya, "from_hour": 19, "to_hour": 21}])
    assert assigned["ok"] is True, assigned

    card = client.get(f"/tools/events/{assigned['event_id']}",
                      headers=_headers(785002)).json()["event"]
    assert card["my_time"]["time_from"] == "19:00"
    assert card["my_time"]["time_to"] == "21:00"
    assert [row["participant_id"] for row in card["participation"]] == [vitya], \
        "роль без schedule.write не получает чужие строки"


def test_чужое_окно_не_приходит(client, monkeypatch):
    """Второй участник видит своё (пустое = всё событие), а не окно первого."""
    _allow(client)
    _prepare(monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 785004, "Чужое Окно Виктор")
        luda = _person(conn, 785005, "Чужое Окно Людмила")

    assigned = _assign(client, "2026-12-23", 18, 22, [vitya, luda],
                       windows=[{"participant_id": vitya, "from_hour": 19, "to_hour": 21}])
    assert assigned["ok"] is True, assigned

    card = client.get(f"/tools/events/{assigned['event_id']}",
                      headers=_headers(785005)).json()["event"]
    assert card["my_time"]["participant_id"] == luda
    assert (card["my_time"]["time_from"], card["my_time"]["time_to"]) == ("18:00", "22:00")
    assert [row["participant_id"] for row in card["participation"]] == [luda]

    # организатор с schedule.write видит обе строки — иначе состав не проверить
    full = client.get(f"/tools/events/{assigned['event_id']}", headers=HEADERS).json()["event"]
    assert {row["participant_id"] for row in full["participation"]} == {vitya, luda}


def test_окно_вне_события_это_422(client, monkeypatch):
    """Личное окно шире события — отказ 422, а не тихая обрезка."""
    _allow(client)
    _prepare(monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 785006, "Вне События Виктор")

    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True, "kind": "rehearsal", "date": "2026-12-24",
        "from_hour": 18, "to_hour": 21, "participants": [vitya],
        "windows": [{"participant_id": vitya, "from_hour": 20, "to_hour": 23}]}).json()
    assert preview["ok"] is False
    assert any(error["code"] == "participation_outside_event" for error in preview["errors"])

    answer = client.post("/tools/events/assign", headers=HEADERS,
                         json={"proposal_id": preview["proposal_id"]})
    assert answer.status_code == 422, answer.text
    assert answer.json()["code"] == "participation_outside_event"


def test_перенос_не_сдвигает_личные_окна_и_предупреждает(client, monkeypatch):
    """Новое окно события не покрывает личное — предупреждение, окно на месте."""
    _allow(client)
    _prepare(monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        vitya = _person(conn, 785007, "Перенос Окно Виктор")

    assigned = _assign(client, "2026-12-25", 18, 22, [vitya],
                       windows=[{"participant_id": vitya, "from_hour": 19, "to_hour": 21}])
    assert assigned["ok"] is True, assigned

    moved = client.post("/tools/events/move", headers=HEADERS, json={
        "event_id": assigned["event_id"], "from_hour": 18, "to_hour": 20}).json()
    assert moved["ok"] is True, moved
    assert any(item["code"] == "personal_windows_outside" for item in moved["warnings"]), moved

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select from_at, to_at from event_participant "
                           "where event_id = %s and participant_id = %s",
                           (assigned["event_id"], vitya)).fetchone()
    assert row[0] is not None and row[1] is not None, "окно не должно исчезнуть"
    # timestamptz приходит в зоне сессии: часы сверяем в зоне сервиса
    from app import core as api

    assert (row[0].astimezone(api.TZ).hour, row[1].astimezone(api.TZ).hour) == (19, 21), \
        "личное окно не сдвигается автоматически"

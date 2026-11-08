"""Выбор тёзки через API: уточнение без повторного объяснения (docs/architecture/52)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


class FakeEvents:
    def insert(self, calendarId, body):
        return self

    def execute(self):
        return {"id": "gcal-y"}


class FakeService:
    def events(self):
        return FakeEvents()


def _allow(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 123456789").fetchone()[0]
        for capability in ("schedule.write", "calendar.write"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тест выбора')
                            on conflict (participant_id, capability) do update set allowed = true""",
                         (pid, capability))
        conn.commit()


def _person(conn, chat_id: int, name: str, short: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.execute("insert into person_alias (participant_id, alias, alias_kind) "
                 "values (%s,%s,'short')", (pid, short))
    conn.commit()
    return pid


def test_выбор_тёзки_обновляет_карточку(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 785001, "Первый Саша", "Саша")
        second = _person(conn, 785002, "Второй Саша", "Саша")

    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True,
        "text": "назначь репетицию с Сашей в среду в 19:00"}).json()
    assert preview["ok"] is False and preview["card"]["ambiguity"]

    chosen = client.post("/tools/events/choose", headers=HEADERS, json={
        "proposal_id": preview["proposal_id"], "participant_id": second}).json()

    assert chosen["ok"] is True, chosen
    assert [p["id"] for p in chosen["card"]["participants"]] == [second]
    assert chosen["proposal_id"] != preview["proposal_id"], "создана новая заявка"


def test_чужой_человек_не_проходит(client, monkeypatch):
    from app import core as api

    _allow(client)
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 785003, "Третий Саша", "Саша")
        _person(conn, 785004, "Четвёртый Саша", "Саша")
        outsider = _person(conn, 785005, "Посторонний Человек", "Посторонний")

    preview = client.post("/tools/events/preview", headers=HEADERS, json={
        "persist": True,
        "text": "назначь репетицию с Сашей в четверг в 19:00"}).json()
    bad = client.post("/tools/events/choose", headers=HEADERS, json={
        "proposal_id": preview["proposal_id"], "participant_id": outsider})
    assert bad.status_code == 400

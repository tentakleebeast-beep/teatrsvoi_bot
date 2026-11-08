"""Регрессии по итогам смоука (docs/54): валидация, dry-run предпросмотра, часы сетки.

Обоснование (Ш-51): файл создан в 02f1853 fix: дефекты смоука — падающие команды, student в схеме, валидация, dry-run предпросмотра."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_min_hours_проверяется(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 788001, "Минус Часы")
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"participants": [pid], "min_hours": -5})
    assert r.status_code == 422


def test_пустой_список_участников_отклоняется(client):
    r = client.post("/tools/schedule/slots", headers=HEADERS, json={"participants": []})
    assert r.status_code == 422
    assert "participants_empty" in r.text


def test_days_проверяется(client):
    assert client.get("/tools/availability/summary?days=-1", headers=HEADERS).status_code == 422
    assert client.get("/tools/calendar/events?days=-5", headers=HEADERS).status_code == 422
    assert client.get("/tools/availability/summary?days=500", headers=HEADERS).status_code == 422


def test_предпросмотр_по_умолчанию_ничего_не_пишет(client, monkeypatch):
    from app import core as api

    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 788002, "Сухой Прогон")
        before = conn.execute("select count(*) from slot_proposal").fetchone()[0]

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-05", "from_hour": 18, "to_hour": 20,
        "participants": [pid]}).json()
    assert body["ok"] is True and body["proposal_id"] is None

    with psycopg.connect(_test_dsn()) as conn:
        after = conn.execute("select count(*) from slot_proposal").fetchone()[0]
    assert after == before, "предпросмотр не должен создавать заявки"


def test_предпросмотр_с_persist_пишет_заявку(client, monkeypatch):
    from app import core as api

    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 788003, "С Заявкой")

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-06", "from_hour": 18, "to_hour": 20,
        "participants": [pid], "persist": True}).json()
    assert body["proposal_id"]


def test_ошибки_ввода_помечены(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 788004, "Ошибка Ввода")
    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "концерт", "date": "2026-12-07", "from_hour": 18, "to_hour": 20,
        "participants": [pid]}).json()
    assert body["input_error"] is True


def test_сетка_месяца_только_читается(client):
    """Ш-04: у сетки месяца нет записи — отметка идёт каноном (З1).

    Раньше этот тест проверял, что `POST /webapp/grid` не пропускает нерабочие
    часы. Маршрута больше нет, поэтому проверяется само отсутствие записи:
    `POST /webapp/grid` — 405, а правило рабочего дня осталось на каноне записи
    (`test_working_day_and_alerts.py`).
    """
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 788005, "Ночные Часы")
    token = client.post("/webapp/auth", json={"init_data": _init(788005)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/webapp/grid", headers=headers,
                    json={"day": "2026-12-08", "status": "busy", "from_hour": 9, "to_hour": 11})
    assert r.status_code == 405, r.text


def _init(chat_id: int) -> str:
    from tests.integration.test_webapp_profile import _api, init_data
    _api()
    return init_data(chat_id)

"""UC-04: расписание недели — поведение calendar API без доступа к Google.

Реальный Google не вызывается: проверяем только ветки «не настроено»
и «нет доступа/учётки». Эндпоинт обязан отвечать структурой ok:false, а не 500.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn


def _use_calendar_ids(monkeypatch, read: str, draft: str = "") -> None:
    from app import core as api_main

    monkeypatch.setattr(api_main, "CALENDAR_ID_READ", read)
    monkeypatch.setattr(api_main, "CALENDAR_ID_DRAFT", draft)


def _break_credentials(monkeypatch) -> None:
    """Указываем на несуществующий файл ключа — доступ к Google невозможен."""
    from app import core as api_main

    monkeypatch.setattr(api_main, "GOOGLE_SA_JSON", "/nonexistent/sa-test.json")


def test_health_без_календарей_в_конфиге(client, auth, monkeypatch):
    _use_calendar_ids(monkeypatch, "", "")
    r = client.get("/tools/calendar/health", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "calendar_id_missing"


def test_events_без_календаря_не_падает(client, auth, monkeypatch):
    _use_calendar_ids(monkeypatch, "", "")
    r = client.get("/tools/calendar/events?days=7", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "calendar_id_missing"


def test_health_без_доступа_к_google(client, auth, monkeypatch):
    _use_calendar_ids(monkeypatch, "test-calendar-id")
    _break_credentials(monkeypatch)
    r = client.get("/tools/calendar/health", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "calendar_unavailable"


def test_events_без_доступа_к_google(client, auth, monkeypatch):
    _use_calendar_ids(monkeypatch, "test-calendar-id")
    _break_credentials(monkeypatch)
    r = client.get("/tools/calendar/events?days=7", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "calendar_unavailable"
    assert "data" not in body


def test_events_без_права_schedule_read_all(client, auth, monkeypatch):
    _use_calendar_ids(monkeypatch, "test-calendar-id")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (777002, 'Студиец', 'troupe', 'active')""")
        conn.execute(
            """insert into participant_role (participant_id, role)
               select id, 'troupe' from participant where tg_chat_id = 777002""")

    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "777002"}
    r = client.get("/tools/calendar/events?days=7", headers=headers)
    assert r.status_code == 403
    assert "schedule.read.all" in r.json()["detail"]
    # health доступен любому действующему участнику
    assert client.get("/tools/calendar/health", headers=headers).status_code == 200

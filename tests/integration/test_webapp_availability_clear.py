"""Очистка недели в Mini App: счётчик считает дни-отметки, а не часы (Ш-53).

Обращение №39: экран говорил «снято 6 отметок», хотя человек видел 2 отмеченных
дня — сервер возвращал `rowcount` удалённых строк `availability_hour`. Здесь
закреплено то, что видит человек: число дней, где отметки были, запись в журнал
даже при нуле и ошибка без числа, когда очистка не удалась.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn, service_today
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    _api()
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _monday() -> dt.date:
    today = service_today()
    return today - dt.timedelta(days=today.weekday())


def _mark(conn, pid: int, day: dt.date, hours: range) -> None:
    for hour in hours:
        conn.execute(
            "insert into availability_hour (participant_id, day, hour, is_free, kind, source) "
            "values (%s,%s,%s,false,'busy','bot')", (pid, day, hour))
    conn.commit()


def test_счётчик_называет_дни_а_не_часы(client):
    """2 отмеченных дня (в одном 3 часа) — «снято 2 отметки», а не 5."""
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 793001, "Очистка Счёт")
        _mark(conn, pid, monday, range(10, 13))                      # 3 часа
        _mark(conn, pid, monday + dt.timedelta(days=1), range(8, 10))  # 2 часа

    headers = _token(client, 793001)
    r = client.post("/webapp/availability/clear", headers=headers,
                    json={"week": monday.isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["cleared"] == 2, "считаем дни-отметки, а не строки часов"
    assert body["hours"] == 5, "часы отдаём отдельным полем — их тоже видно в журнале"

    week = client.get(f"/webapp/availability/week?start={monday.isoformat()}", headers=headers)
    days = week.json()["days"]
    assert all(day["marked"] is False for day in days), "после очистки все дни «не отмечено»"


def test_пустая_неделя_пишет_ноль_и_журнал(client):
    """0 отметок — это «снято 0 отметок», и событие всё равно остаётся в журнале."""
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 793002, "Очистка Пусто")

    headers = _token(client, 793002)
    body = client.post("/webapp/availability/clear", headers=headers,
                       json={"week": monday.isoformat()}).json()
    assert body["ok"] is True and body["cleared"] == 0 and body["hours"] == 0

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "select actor_id, entity_id, after from audit_event "
            "where action = 'availability.clear' order by id desc limit 1").fetchone()
    assert row is not None, "нажатие «Очистить» на пустой неделе тоже событие"
    assert row[0] == pid
    assert row[1] == f"{monday.isoformat()}:{(monday + dt.timedelta(days=6)).isoformat()}"
    assert row[2]["cleared"] == 0 and row[2]["hours"] == 0


def test_очистка_не_удалась_числа_нет_есть_код(client, monkeypatch):
    """Отказ базы — ошибка с машинным кодом, без поля `cleared`."""
    from app import core as api
    from app.main import app
    from fastapi.testclient import TestClient

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 793003, "Очистка Отказ")
    headers = _token(client, 793003)

    class BrokenPool:
        def connection(self):
            raise psycopg.OperationalError("база недоступна")

    monkeypatch.setattr(api, "pool", BrokenPool())
    probe = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = probe.post("/webapp/availability/clear", headers=headers,
                   json={"week": _monday().isoformat()})

    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False and body["code"] == "internal_error"
    assert "cleared" not in body, "числа в ответе с ошибкой быть не должно"
    assert body["request_id"] == r.headers["X-Request-ID"]

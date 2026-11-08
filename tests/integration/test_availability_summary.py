"""Сводка «свободно по дням» (FR-C5, обращение №11)."""

import datetime as dt

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn, service_today

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _cell(conn, pid: int, day, hour: int, kind: str, is_free: bool) -> None:
    conn.execute("insert into availability_hour"
                 " (participant_id, day, hour, is_free, kind, source)"
                 " values (%s,%s,%s,%s,%s,'bot')", (pid, day, hour, is_free, kind))


def test_сводка_считает_людей_и_не_прячет_пустые_дни(client):
    today = service_today()
    with psycopg.connect(_test_dsn()) as conn:
        free = _person(conn, 791001, "Сводка Свободен")
        busy = _person(conn, 791002, "Сводка Занят")
        _cell(conn, free, today, 10, 'free', True)
        _cell(conn, free, today, 11, 'preferred', True)
        _cell(conn, busy, today, 10, 'busy', False)
        # «не уверен» лежит с is_free = true, но свободным не считается
        _cell(conn, busy, today, 11, 'uncertain', True)
        # вне рабочего дня — отдельный счётчик, а не свободные часы
        _cell(conn, free, today, 3, 'free', True)
        conn.commit()

    body = client.get("/tools/availability/summary?days=7", headers=HEADERS).json()
    assert body["ok"] is True
    assert body["days"] == 7 and len(body["data"]) == 7
    first = body["data"][0]
    assert first["day"] == today.isoformat()
    assert first["known_cnt"] == 4       # 3 в рабочем дне + 1 ночной
    assert first["free_cnt"] == 2        # free + preferred, без uncertain и ночного
    assert first["people_cnt"] == 2
    assert first["outside_hours"] == 1
    # день без отметок не исчезает: раньше из-за этого список выглядел сломанным
    assert body["data"][1]["known_cnt"] == 0 and body["data"][1]["free_cnt"] == 0
    assert [r["day"] for r in body["data"]] == [
        (today + dt.timedelta(days=i)).isoformat() for i in range(7)]


def test_отметка_неуверенности_не_делает_человека_свободным(client):
    today = service_today()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 791003, "Сводка Неуверен")

    r = client.post(
        "/tools/availability/commit",
        headers={**HEADERS, "X-Actor-Chat-Id": "791003"},
        json={"chat_id": 791003, "day": today.isoformat(),
              "start": 10, "end": 11, "kind": "uncertain"})
    assert r.status_code == 200, r.text
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select is_free, kind from availability_hour"
                           " where participant_id = %s", (pid,)).fetchone()
    assert row == (False, 'uncertain')


def test_fr_c7_сводка_сообщает_покрытие(client):
    """FR-C7: сводка отдаёт, сколько людей отчиталось, и вердикт «верить или нет»."""
    today = service_today()
    with psycopg.connect(_test_dsn()) as conn:
        p1 = _person(conn, 794001, "Покрытие Один")
        _person(conn, 794002, "Покрытие Два")
        # архивный не должен попадать в ожидаемые
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (794003, 'Покрытие Архив', 'архив', 'troupe', 'archived')")
        _cell(conn, p1, today, 10, "busy", False)
        conn.commit()

    with psycopg.connect(_test_dsn()) as conn:
        # ожидаемые — активные участники; архивный не считается ни в числителе, ни в знаменателе
        expected = conn.execute(
            "select count(*) from participant where status = 'active'").fetchone()[0]
        archived = conn.execute(
            "select count(*) from participant where status = 'archived'").fetchone()[0]

    body = client.get("/tools/availability/summary?days=7", headers=HEADERS).json()
    cov = body["coverage"]
    assert archived == 1
    assert cov["expected"] == expected and cov["expected"] >= 2
    assert cov["marked"] == 1            # отчитался один из наших
    assert cov["trustworthy"] is False
    assert "1 из" in cov["line"] and "нужно не меньше 80%" in cov["line"]

"""События календаря вычитаются из занятости при поиске окон (docs/architecture/45)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _free(conn, pid: int, day: str, hours: list[int]) -> None:
    for hour in hours:
        conn.execute("""insert into availability_hour
                          (participant_id, day, hour, is_free, kind, source)
                        values (%s,%s,%s,true,'declared','bot')""", (pid, day, hour))


def test_событие_убирает_окно(client, monkeypatch):
    from app import core as api

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 791001, "Событие Первый")
        b = _person(conn, 791002, "Событие Второй")
        for pid in (a, b):
            _free(conn, pid, "2026-12-14", list(range(17, 23)))
        conn.commit()

    # показ с 19:00 занимает обоих
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [{
        "id": "ev-1", "summary": "Показ - Тест",
        "start": {"dateTime": "2026-12-14T19:00:00+04:00"},
        "end": {"dateTime": "2026-12-14T22:00:00+04:00"},
        "extendedProperties": {"private": {
            "svoi_kind": "show",
            "svoi_participant_ids": f"{a},{b}"}},
    }])

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "participants": [a, b], "date_from": "2026-12-14", "date_to": "2026-12-14",
        "min_hours": 3}).json()

    assert body["meta"]["events"] == 1
    assert body["meta"]["blocked_hours"] > 0
    for window in body["windows"]:
        assert window["to_hour"] <= 19 or window["from_hour"] >= 22, \
            f"окно {window} пересекается с показом"


def test_без_событий_поиск_работает_как_раньше(client, monkeypatch):
    from app import core as api
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [])

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 791003, "Событие Третий")
        _free(conn, a, "2026-12-15", list(range(18, 23)))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "participants": [a], "date_from": "2026-12-15", "date_to": "2026-12-15",
        "min_hours": 2}).json()
    assert body["windows"], "без событий окно должно найтись"
    assert body["meta"]["events"] == 0


def test_блокировку_можно_отключить(client, monkeypatch):
    from app import core as api
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [{
        "id": "ev-2", "summary": "Показ",
        "start": {"dateTime": "2026-12-16T18:00:00+04:00"},
        "end": {"dateTime": "2026-12-16T23:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_participant_ids": "791004"}},
    }])

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 791004, "Событие Четвёртый")
        _free(conn, a, "2026-12-16", list(range(18, 23)))
        conn.commit()

    off = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "participants": [a], "date_from": "2026-12-16", "date_to": "2026-12-16",
        "min_hours": 2, "block_events": False}).json()
    assert off["windows"] and off["meta"]["events"] == 0


def test_окно_для_репетиции_учитывает_людей_и_события(client, monkeypatch):
    """Область участников: окно режется и отметками людей, и событиями календаря.

    Оба человека свободны 08:00–23:00, но показ 18:00–20:00 занимает одного —
    окно рвётся на этих часах, хотя по отметкам человек свободен
    (docs/architecture/40 §3, docs/architecture/45 §3).
    """
    from app import core as api

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 791005, "Репетиция Первый")
        b = _person(conn, 791006, "Репетиция Второй")
        for pid in (a, b):
            conn.execute("update participant set travel_minutes = 0 where id = %s", (pid,))
            _free(conn, pid, "2026-12-17", list(range(8, 24)))
        conn.commit()

    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [{
        "id": "ev-3", "summary": "Показ - Тест",
        "start": {"dateTime": "2026-12-17T18:00:00+04:00"},
        "end": {"dateTime": "2026-12-17T20:00:00+04:00"},
        "extendedProperties": {"private": {
            "svoi_kind": "show", "svoi_participant_ids": str(a)}},
    }])

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rehearsal", "participants": [a, b],
        "date_from": "2026-12-17", "date_to": "2026-12-17", "min_hours": 2}).json()

    assert body["mode"] == "policy" and body["meta"]["events"] == 1
    spans = [(w["from_hour"], w["to_hour"]) for w in body["windows"]]
    assert spans, body
    for start, end in spans:
        assert end <= 18 or start >= 20, f"окно {start}–{end} пересекает показ"
    assert (8, 18) in spans, "до показа обоим свободно"
    assert (20, 24) in spans, "после показа окно возвращается, включая клетку 23:00"

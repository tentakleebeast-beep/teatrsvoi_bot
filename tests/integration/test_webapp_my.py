"""Личный лист занятости: часы в строках, дни в колонках (docs/development/48).

Отметки кладутся прямо в `availability_hour`: лист — чтение, и проверяется именно
чтение. Пишет пусть канон записи (З1) — у него свои тесты; здесь важно, что лист
показывает ровно те часы, которые лежат в базе, и ничего не досочиняет.
"""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _mark(participant_id: int, day: str, hours: list[int], kind: str) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        for hour in hours:
            conn.execute("""
                insert into availability_hour
                  (participant_id, day, hour, is_free, kind, source)
                values (%s,%s,%s,%s,%s,'bot')""",
                         (participant_id, day, hour, kind in ("free", "preferred"), kind))
        conn.commit()


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_личный_лист_отдаёт_свой_месяц(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782001, "Лист Первый")
    headers = _token(client, 782001)

    body = client.get("/webapp/my?month=2026-11", headers=headers).json()
    assert body["ok"] is True
    assert len(body["days"]) == 30
    assert body["hours"] == [8, 23]
    assert body["name"] == "Лист Первый"
    assert body["cells"] == {}


def test_отметка_часа_видна_в_листе(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 782002, "Лист Час")
    headers = _token(client, 782002)

    _mark(pid, "2026-11-10", [14], "busy")

    body = client.get("/webapp/my?month=2026-11", headers=headers).json()
    assert body["cells"]["2026-11-10"]["14"] == "busy"
    assert "13" not in body["cells"]["2026-11-10"], "соседний час не тронут"


def test_диапазон_часов(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 782003, "Лист Диапазон")
    headers = _token(client, 782003)

    _mark(pid, "2026-11-11", [8, 9, 10, 11], "free")
    body = client.get("/webapp/my?month=2026-11", headers=headers).json()
    marked = sorted(int(hour) for hour in body["cells"]["2026-11-11"])
    assert marked == [8, 9, 10, 11]


def test_чужой_лист_недоступен(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782004, "Лист Свой")
        other = _person(conn, 782005, "Лист Чужой")
    headers = _token(client, 782004)

    body = client.get("/webapp/my?month=2026-11", headers=headers).json()
    assert body["name"] == "Лист Свой", "лист всегда свой, параметра чужого человека нет"

    with psycopg.connect(_test_dsn()) as conn:
        mine = conn.execute("select id from participant where tg_chat_id = 782004").fetchone()[0]
    _mark(mine, "2026-11-12", [9], "busy")
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute("select participant_id from availability_hour "
                            "where day = '2026-11-12'").fetchall()
    assert all(row[0] != other for row in rows)

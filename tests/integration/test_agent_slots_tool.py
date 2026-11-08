"""Инструмент окон: `find_slots` считает код, а не модель (карта «Озадачить», п.2).

Инструмент обязан звать тот же сервис, что экран поиска окон (`/tools/schedule/slots`),
а не пересчитывать занятость по-своему: иначе «окна» в чате и на экране разойдутся.

Обоснование: docs/architecture/40, AC «Озадачить».
"""

from __future__ import annotations

import psycopg
import pytest
from app.agently import registry
from psycopg.rows import dict_row


@pytest.fixture()
def conn(test_db: str):
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        yield connection


def _person(conn, chat_id: int, name: str) -> dict:
    row = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status,"
        " travel_minutes) values (%s,%s,%s,'troupe','active',0)"
        " returning id, display_name",
        (chat_id, name, f"person-{chat_id}")).fetchone()
    conn.commit()
    return {"id": row["id"], "display_name": row["display_name"], "roles": ["troupe"],
            "caps": ["*"]}


def _free(conn, participant_id: int, day: str, hours: list[int]) -> None:
    for hour in hours:
        conn.execute(
            "insert into availability_hour (participant_id, day, hour, is_free, kind)"
            " values (%s,%s,%s,true,'free')"
            " on conflict (participant_id, day, hour) do update set is_free = true",
            (participant_id, day, hour))
    conn.commit()


def test_окна_строятся_по_занятости_и_называют_людей(client, conn) -> None:
    mark = _person(conn, 571001, "Марк")
    masha = _person(conn, 571002, "Маша")
    day = "2026-11-19"
    _free(conn, mark["id"], day, [18, 19])
    _free(conn, masha["id"], day, [18, 19])

    result = registry.execute(conn, mark, "find_slots", {
        "participants": ["Марк", "Маша"], "policy": "rehearsal",
        "date_from": day, "date_to": day, "min_hours": 2})

    assert result["ok"] is True
    assert result["windows"], result["meta"]
    window = result["windows"][0]
    assert window["day"] == day and window["from_hour"] == 18
    assert "18:00–20:00" in " ".join(result["text"])


def test_занятый_час_ломает_окно(client, conn) -> None:
    mark = _person(conn, 571003, "Пётр")
    _free(conn, mark["id"], "2026-11-20", [18])
    conn.execute("insert into availability_hour (participant_id, day, hour, is_free, kind)"
                 " values (%s,'2026-11-20',19,false,'busy')", (mark["id"],))
    conn.commit()

    result = registry.execute(conn, mark, "find_slots", {
        "participants": [str(mark["id"])], "policy": "rehearsal",
        "date_from": "2026-11-20", "date_to": "2026-11-20", "min_hours": 2})

    assert result["ok"] is True
    assert result["windows"] == [], "час занятости не даёт двухчасового окна"


def test_без_права_окон_инструмента_нет(conn) -> None:
    who = _person(conn, 571004, "Без прав")
    who["caps"] = []

    result = registry.execute(conn, who, "find_slots", {"policy": "rehearsal"})

    assert result["ok"] is False and result["error"] == "forbidden"

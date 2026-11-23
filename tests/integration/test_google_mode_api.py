"""Маршруты `/tools/google`: смена режима и свежесть глазами реальной роли.

Обоснование: docs/development/186 §2.3, AC-11, AC-17; docs/development/190 §10.4.
Право маршрута — существующая возможность `admin.panel` (`db/init/043`), а не строка
`*`: возможности `*` в справочнике нет, её получает только владелец без строки
участника, поэтому прежняя проверка отдавала настоящему администратору 403.
"""

from __future__ import annotations

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import _test_dsn


@pytest.fixture(autouse=True)
def _clean_google_settings():
    """`app_setting` не чистится между тестами — режим возвращаем сами."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("delete from app_setting where key like 'google.%'")
    yield
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("delete from app_setting where key like 'google.%'")


def _person(conn, chat_id: int, name: str, role: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _headers(chat_id: int, token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Actor-Chat-Id": str(chat_id)}


def test_администратор_переключает_режим_и_пишет_аудит(client):
    """AC-11: админ с правом `admin.panel` входит, переход в `target` с выгрузкой — 200."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 555001, "Режимный Админ", "admin")

    headers = _headers(555001, TOKEN)
    answer = client.post("/tools/google/mode", headers=headers,
                         json={"mode": "target", "backup": "https://drive.example/export.zip"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["mode"] == "target"

    with psycopg.connect(_test_dsn()) as conn:
        value = conn.execute("select value from app_setting where key = 'google.mode'").fetchone()
        audit = conn.execute("select count(*) from audit_event "
                             "where action = 'google.mode.set'").fetchone()[0]
    assert value[0] == "target"
    assert audit == 1


def test_переход_без_выгрузки_запрещён(client):
    """AC-11: `target` без `backup` — 400 `mode_backup_required`, режим не меняется."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 555002, "Режимный Админ 2", "admin")

    answer = client.post("/tools/google/mode", headers=_headers(555002, TOKEN),
                         json={"mode": "target"})
    assert answer.status_code == 400, answer.text
    assert answer.json()["code"] == "mode_backup_required"
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select value from app_setting where key = 'google.mode'").fetchone()
    assert row is None or row[0] == "transitional"


def test_участник_без_права_получает_403(client):
    """Маршрут закрыт не «для всех», а проверкой права: у труппы `admin.panel` нет."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 555003, "Режимный Артист", "troupe")

    answer = client.post("/tools/google/mode", headers=_headers(555003, TOKEN),
                         json={"mode": "transitional"})
    assert answer.status_code == 403, answer.text


def test_свежесть_отдаёт_календарь_с_полями_190(client, auth):
    """AC-17/190 §10.4: `calendar.read` с `as_of`, `last_edit_time`, `stale`, `error`."""
    answer = client.get("/tools/google/freshness", headers=auth)
    assert answer.status_code == 200, answer.text
    sources = {item["key"]: item for item in answer.json()["sources"]}
    assert "calendar.read" in sources
    entry = sources["calendar.read"]
    assert entry["as_of"] is None or isinstance(entry["as_of"], str)
    assert "last_edit_time" in entry
    assert entry["stale"] is True, "без чтения источник не может быть свежим"
    assert entry["error"] is None

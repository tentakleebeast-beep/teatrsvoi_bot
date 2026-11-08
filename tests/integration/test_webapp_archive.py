"""Архив: дата события и возврат в правильный статус.

Дефект проды 30.10.2026: колонка «В архиве с» была пустой, потому что поля
archived_at нет в схеме, а мой запрос к журналу шёл по несуществующей колонке —
`/webapp/admin/overview` отвечал 500. Тест ловит оба случая.

Правила и требования раздела: docs/development/73-admin-panel-webapp.md.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ADMIN_CHAT = 123456789


def _admin_headers(client) -> dict:
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()
        if row is None:
            pid = conn.execute(
                "insert into participant (tg_chat_id, display_name, person_key, role, status) "
                "values (%s,'Тест Админ',%s,'admin','active') returning id",
                (ADMIN_CHAT, make_key("Тест Админ"))).fetchone()["id"]
        else:
            pid = row["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s,'admin') "
                     "on conflict do nothing", (pid,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(ADMIN_CHAT)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _person(name: str, status: str = "active") -> int:
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        pid = conn.execute(
            "insert into participant (display_name, person_key, role, status) "
            "values (%s,%s,'troupe',%s) returning id",
            (name, make_key(name), status)).fetchone()["id"]
        conn.commit()
    return pid


def test_архив_показывает_дату_и_возвращает_статус(client):
    headers = _admin_headers(client)
    pid = _person("Архивный Тест")

    archived = client.post("/webapp/admin/archive",
                           json={"participant_id": pid, "reason": "test"}, headers=headers)
    assert archived.status_code == 200, archived.text

    overview = client.get("/webapp/admin/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    rows = overview.json()["blocks"]["archive"]["data"]
    entry = next(item for item in rows if item["id"] == pid)
    assert entry.get("archived_at"), "колонка «В архиве с» пустая"

    back = client.post("/webapp/admin/unarchive",
                       json={"participant_id": pid, "reason": "undo"}, headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "active"


def test_отклонённая_заявка_возвращается_в_ожидающие(client):
    headers = _admin_headers(client)
    pid = _person("Отклонённый Тест", status="pending")
    client.post("/webapp/admin/archive", json={"participant_id": pid, "reason": "reject"},
                headers=headers)
    back = client.post("/webapp/admin/unarchive",
                       json={"participant_id": pid, "reason": "undo"}, headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "pending", "возврат обошёл бы регистрацию"

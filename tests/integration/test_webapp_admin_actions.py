"""Действия админ-панели Mini App (Ш-33, docs/development/73).

Проверяем, что страница не только читает: заявки, роли и тариф меняются теми же
обработчиками и с теми же правами, что и кнопки бота. Очереди крупных сумм в панели
нет — раздела `approvals` в ответе быть не должно.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "troupe",
            status: str = "active") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,%s) returning id",
        (chat_id, name, make_key(name), role, status)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _allow(participant_id: int, capability: str) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant_capability_override "
            "(participant_id, capability, allowed, reason) values (%s,%s,true,'тест Ш-33') "
            "on conflict (participant_id, capability) do update set allowed = true",
            (participant_id, capability))
        conn.commit()


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_без_права_действие_закрыто(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783001, "Действие Без Прав")
        target = _person(conn, 783002, "Заявка Без Прав", status="pending")
    headers = _token(client, 783001)

    r = client.post("/webapp/admin/approve",
                    json={"participant_id": target, "approve": True}, headers=headers)
    assert r.status_code == 403
    assert "registration.approve" in r.json()["detail"]

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from participant where id = %s",
                              (target,)).fetchone()[0]
        denied = conn.execute(
            "select count(*) from audit_event where action = 'access.denied' "
            "and after->>'via' = 'webapp'").fetchone()[0]
    assert status == "pending", "без права заявка не должна меняться"
    assert denied == 1, "отказ в праве должен попасть в аудит"


def test_заявку_подтверждают_из_панели(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783010, "Действие Админ", role="admin")
        target = _person(conn, 783011, "Заявка Из Панели", status="pending")
    headers = _token(client, 783010)

    r = client.post("/webapp/admin/approve",
                    json={"participant_id": target, "approve": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from participant where id = %s",
                              (target,)).fetchone()[0]
        actions = [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(target),))]
    assert status == "active"
    assert "participant.approve" in actions


def test_заявку_отклоняют_из_панели(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783020, "Действие Админ Отказ", role="admin")
        target = _person(conn, 783021, "Заявка Отклонённая", status="pending")
    headers = _token(client, 783020)

    body = client.post("/webapp/admin/approve",
                       json={"participant_id": target, "approve": False},
                       headers=headers).json()
    assert body["status"] == "archived"


def test_роль_выдаётся_из_панели(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783030, "Действие Права", role="admin")
        target = _person(conn, 783031, "Новый Организатор")
    headers = _token(client, 783030)

    body = client.post("/webapp/admin/grant",
                       json={"participant_id": target, "role": "org", "enabled": True},
                       headers=headers).json()
    assert body["ok"] is True
    assert "org" in body["roles"]

    with psycopg.connect(_test_dsn()) as conn:
        actions = [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(target),))]
    assert "rbac.role.grant" in actions


def test_роль_без_права_не_выдаётся(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783040, "Права Без Прав")
        target = _person(conn, 783041, "Цель Без Прав")
    headers = _token(client, 783040)

    r = client.post("/webapp/admin/grant",
                    json={"participant_id": target, "role": "admin", "enabled": True},
                    headers=headers)
    assert r.status_code == 403
    # Отказ сказан словами (Б3): человек знает, кто меняет роли, а код остаётся
    # машинным для клиента (V-037, D4).
    assert r.json()["code"] == "participants_manage_forbidden"
    assert r.json()["detail"] == "Роли и дисциплины педагога меняет администратор."


def test_тариф_ставится_из_панели(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783050, "Действие Тариф", role="admin")
        target = _person(conn, 783051, "Участник С Тарифом")
    headers = _token(client, 783050)

    body = client.post("/webapp/admin/tariff",
                       json={"participant_id": target, "tariff": 1500}, headers=headers).json()
    assert body["ok"] is True
    assert body["dues_tariff"] == 1500
    assert body["dues_tariff_from"], "тариф должен получить дату начала"

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select dues_tariff, dues_tariff_from from participant "
                           "where id = %s", (target,)).fetchone()
    assert float(row[0]) == 1500
    assert row[1].day == 1, "новый тариф действует с первого числа следующего месяца"


def test_панель_отдаёт_разрешённые_действия(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783070, "Действие Обзор", role="admin")
        reader = _person(conn, 783071, "Действие Читатель")
    _allow(reader, "participants.view")

    admin_view = client.get("/webapp/admin/overview", headers=_token(client, 783070)).json()
    assert admin_view["actions"] == {"approve_pending": True, "manage_rights": True,
                                     "set_tariff": True, "accrue_dues": True,
                                     "retry_sheet": True, "issue_codes": True}

    reader_view = client.get("/webapp/admin/overview", headers=_token(client, 783071)).json()
    assert reader_view["actions"] == {"approve_pending": False, "manage_rights": False,
                                      "set_tariff": False, "accrue_dues": False,
                                      "retry_sheet": False, "issue_codes": False}
    assert "approvals" not in reader_view["blocks"], "чужая очередь сумм не отдаётся"

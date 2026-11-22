"""UC-05/06: админ-панель — заявки, справочник прав, аудит, выдача прав (docs/development/28).

Проверяем контракты /admin/pending, /admin/capabilities, /tools/audit/recent,
/admin/grant и отказы 403 без нужных возможностей.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


def _insert_person(chat_id: int | None, name: str, role: str = "troupe",
                   status: str = "active") -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        person_id = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (%s, %s, %s, %s) returning id""",
            (chat_id, name, role, status)).fetchone()[0]
        conn.execute(
            "insert into participant_role (participant_id, role) values (%s, %s)",
            (person_id, role))
    return person_id


def _make_actor_admin() -> None:
    """Фикстурный org не имеет participants.manage — добавляем роль admin."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant_role (participant_id, role)
               select id, 'admin' from participant where tg_chat_id = 123456789
               on conflict do nothing""")


def _troupe_headers(chat_id: int = 750001) -> dict:
    _insert_person(chat_id, "Студиец Без Прав", "troupe", "active")
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _admin_session(client) -> dict:
    """Сессия Mini App администратора: код выпускается только оттуда (§4.3.3)."""
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    _make_actor_admin()
    token = client.post("/webapp/auth", json={"init_data": init_data(123456789)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _code_recipient() -> int:
    """Человек без Telegram — на него выписывается персональный код."""
    return _insert_person(None, "Кодов Получатель")


# --- заявки -----------------------------------------------------------------

def test_путей_заявок_больше_нет(client, auth):
    """Заявки сняты из бота (docs/development/182 §4.1): пути отвечают 404."""
    assert client.get("/admin/pending", headers=auth).status_code == 404
    assert client.post("/admin/approve", json={"participant_id": 1, "approve": True},
                       headers=auth).status_code == 404


# --- справочник возможностей ------------------------------------------------

def test_capabilities_содержит_справочник_и_матрицу(client, auth):
    body = client.get("/admin/capabilities", headers=auth).json()
    assert body["ok"] is True
    codes = {c["code"] for c in body["capabilities"]}
    assert {"budget.read", "participants.view", "audit.view"} <= codes
    roles = {m["role"] for m in body["matrix"]}
    assert roles == {"admin", "org", "troupe", "master"}


def test_capabilities_без_прав(client, auth):
    r = client.get("/admin/capabilities", headers=_troupe_headers())
    assert r.status_code == 403


# --- аудит ------------------------------------------------------------------

def test_аудит_пишется_после_действия(client, auth):
    # действие: выпуск персонального кода из Mini App
    person = _code_recipient()
    assert client.post("/webapp/admin/invite", json={"participant_id": person},
                       headers=_admin_session(client)).status_code == 200

    body = client.get("/tools/audit/recent?limit=5", headers=auth).json()
    assert body["ok"] is True
    assert body["data"], "журнал аудита пуст после действия"
    actions = [e["action"] for e in body["data"]]
    assert "invite.create" in actions
    assert all(e["hash"] for e in body["data"])


def test_аудит_по_умолчанию_не_виден(client, auth):
    r = client.get("/tools/audit/recent", headers=_troupe_headers())
    assert r.status_code == 403
    assert "audit.view" in r.json()["detail"]


def test_аудит_ограничивает_limit(client, auth):
    session = _admin_session(client)
    for name in ("Лимит Один", "Лимит Два"):
        person = _insert_person(None, name)
        client.post("/webapp/admin/invite", json={"participant_id": person}, headers=session)
    body = client.get("/tools/audit/recent?limit=1", headers=auth).json()
    assert len(body["data"]) == 1


# --- выдача и снятие прав ---------------------------------------------------

def test_grant_выдаёт_и_снимает_capability(client, auth):
    _make_actor_admin()
    person_id = _insert_person(750020, "Кому Выдаём", "troupe", "active")

    granted = client.post("/admin/grant",
                          json={"participant_id": person_id,
                                "capability": "budget.read", "allowed": True,
                                "reason": "тест"}, headers=auth).json()
    assert granted["ok"] is True
    assert "budget.read" in granted["capabilities"]

    with psycopg.connect(_test_dsn()) as conn:
        actions = [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(person_id),))]
    assert "rbac.capability.grant" in actions

    revoked = client.post("/admin/grant",
                          json={"participant_id": person_id,
                                "capability": "budget.read", "allowed": False,
                                "reason": "тест"}, headers=auth).json()
    assert "budget.read" not in revoked["capabilities"]

    with psycopg.connect(_test_dsn()) as conn:
        actions = [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(person_id),))]
    assert "rbac.capability.revoke" in actions


def test_grant_добавляет_роль(client, auth):
    _make_actor_admin()
    person_id = _insert_person(750021, "Будущий Орг", "troupe", "active")

    body = client.post("/admin/grant",
                       json={"participant_id": person_id, "role": "org"},
                       headers=auth).json()
    assert body["ok"] is True
    assert "org" in body["roles"]
    assert "budget.read" in body["capabilities"]


def test_grant_несуществующего_участника(client, auth):
    _make_actor_admin()
    r = client.post("/admin/grant",
                    json={"participant_id": 999999, "capability": "budget.read",
                          "allowed": True}, headers=auth)
    assert r.status_code == 404


def test_grant_без_прав_manage(client, auth):
    # у org есть participants.view, но нет participants.manage
    person_id = _insert_person(750022, "Цель", "troupe", "active")
    r = client.post("/admin/grant",
                    json={"participant_id": person_id, "capability": "budget.read",
                          "allowed": True}, headers=auth)
    assert r.status_code == 403
    assert "participants.manage" in r.json()["detail"]


def test_grant_студенту_отклоняется_валидацией(client, auth):
    _make_actor_admin()
    person_id = _insert_person(750023, "Цель", "troupe", "active")
    r = client.post("/admin/grant",
                    json={"participant_id": person_id, "role": "student"}, headers=auth)
    assert r.status_code == 422


# --- отзыв роли (FR-A6) -----------------------------------------------------

def _allow_manage(actor_chat: int = 123456789) -> None:
    """Выдаём действующему в фикстуре участнику право управления (только на тест)."""
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (actor_chat,)).fetchone()[0]
        conn.execute("""insert into participant_capability_override
                          (participant_id, capability, allowed, reason)
                        values (%s, 'participants.manage', true, 'тест админ-панели')
                        on conflict (participant_id, capability) do update set allowed = true""",
                     (pid,))
        conn.commit()


def _new_person(client, auth, chat_id: int, name: str, role: str) -> int:
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s, %s, %s, %s, 'active') returning id",
            (chat_id, name, make_key(name), role)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s, %s)",
                     (pid, role))
        conn.commit()
    return pid


def test_роль_снимается(client, auth):
    _allow_manage()
    import psycopg

    from tests.conftest import _test_dsn

    pid = _new_person(client, auth, 770001, "Тестовый Роль", "troupe")
    with psycopg.connect(_test_dsn()) as conn:          # вторая роль, чтобы снятие было законным
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'org')",
                     (pid,))
        conn.commit()

    r = client.post("/admin/grant",
                    json={"participant_id": pid, "role": "troupe", "enabled": False},
                    headers=auth)
    assert r.status_code == 200
    assert r.json()["roles"] == ["org"]


def test_последнюю_роль_снять_нельзя(client, auth):
    _allow_manage()
    pid = _new_person(client, auth, 770002, "Тестовый Один", "troupe")
    r = client.post("/admin/grant",
                    json={"participant_id": pid, "role": "troupe", "enabled": False},
                    headers=auth)
    assert r.status_code == 400


def test_нельзя_снять_единственную_роль_у_админа(client, auth):
    _allow_manage()
    pid = _new_person(client, auth, 770003, "Тестовый Админ", "admin")
    r = client.post("/admin/grant",
                    json={"participant_id": pid, "role": "admin", "enabled": False},
                    headers=auth)
    assert r.status_code == 400
    assert "last_role" in r.text

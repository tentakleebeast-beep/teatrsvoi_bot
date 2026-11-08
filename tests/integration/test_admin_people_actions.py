"""Люди и роли: отзыв роли, блокировка, архив — один путь записи у чата и экрана.

Карта `t_8f49da0d` (Ш-07), реестр `docs/architecture/25` §9.4 — строки Л4/Л6/Л7.
Обоснование: FR-A6 (отзыв роли с аудитом), FR-A7 (архив и возврат), FR-A9 (блокировка).

Проверяем не «маршрут отвечает 200», а следствия:
  * отзыв роли снимает возможности и оставляет строку роли с пометкой, а не удаляет её
    (`docs/architecture/11` §5 п.1);
  * `blocked` закрывает доступ сразу — и в чате (`core.current`), и в Mini App;
  * архив из чата и с экрана дают один и тот же результат, потому что зовут одну функцию.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ADMIN_CHAT = 123456789


def _person(chat_id: int | None, name: str, role: str = "troupe",
            status: str = "active") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,%s,%s) returning id",
            (chat_id, name, make_key(name), role, status)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                     (pid, role))
        conn.commit()
    return pid


def _add_role(pid: int, role: str) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s) "
                     "on conflict do nothing", (pid, role))
        conn.commit()


def _admin() -> int:
    """Действующий админ: фикстурный организатор плюс роль admin."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,'admin') "
                     "on conflict do nothing", (pid,))
        conn.commit()
    return pid


def _chat(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _webapp(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _row(pid: int) -> dict:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select status, is_active from participant where id = %s",
                           (pid,)).fetchone()
    return {"status": row[0], "is_active": row[1]}


def _audit(pid: int) -> list[str]:
    with psycopg.connect(_test_dsn()) as conn:
        return [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s order by id", (str(pid),))]


# ------------------------------------------------------------------ Л4: отзыв роли
def test_отзыв_роли_прекращает_доступ_и_пишется_в_аудит(client):
    """FR-A6: отзыв роли снимает её возможности, оставляет строку-пометку и аудит."""
    _api()
    _admin()
    target = _person(784100, "Отзыв Роли", role="troupe", status="active")
    _add_role(target, "org")

    before = client.get("/tools/me/capabilities", headers=_chat(784100)).json()
    assert "budget.write" in before["capabilities"], "у org есть budget.write"

    body = client.post("/admin/grant",
                       json={"participant_id": target, "role": "org", "enabled": False,
                             "reason": "ушёл с должности"}, headers=_chat(ADMIN_CHAT)).json()
    assert body["ok"] is True
    assert "org" not in body["roles"]
    assert "budget.write" not in body["capabilities"]

    after = client.get("/tools/me/capabilities", headers=_chat(784100)).json()
    assert "budget.write" not in after["capabilities"], "отзыв роли закрывает доступ сразу"
    assert after["roles"] == ["troupe"]

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "select revoked_at, revoke_reason from participant_role "
            "where participant_id = %s and role = 'org'", (target,)).fetchone()
    assert row is not None, "строка роли не удаляется, а помечается (docs/architecture/11 §5 п.1)"
    assert row[0] is not None and row[1] == "ушёл с должности"

    assert "rbac.role.revoke" in _audit(target)


def test_повторная_выдача_возвращает_отозванную_роль(client):
    """FR-A6: выдача после отзыва — та же строка, право снова действует."""
    _api()
    _admin()
    target = _person(784101, "Возврат Роли", role="troupe", status="active")
    _add_role(target, "org")
    headers = _chat(ADMIN_CHAT)

    client.post("/admin/grant", json={"participant_id": target, "role": "org",
                                      "enabled": False}, headers=headers)
    back = client.post("/admin/grant", json={"participant_id": target, "role": "org"},
                       headers=headers).json()
    assert "org" in back["roles"]
    assert "budget.write" in back["capabilities"]

    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute("select count(*), min(revoked_at) from participant_role "
                            "where participant_id = %s and role = 'org'",
                            (target,)).fetchone()
    assert rows[0] == 1, "вторая строка роли не заводится: ключ (participant_id, role)"
    assert rows[1] is None


# ------------------------------------------------------------------ Л7: блокировка
def test_блокировка_немедленно_закрывает_доступ(client):
    """FR-A9: blocked закрывает и чат, и Mini App, а разблокировка возвращает статус."""
    _api()
    _admin()
    target = _person(784200, "Блокировка Цели", role="troupe", status="active")
    webapp = _webapp(client, 784200)
    assert client.get("/tools/me/capabilities", headers=_chat(784200)).status_code == 200
    assert client.get("/webapp/my", headers=webapp).status_code == 200

    headers = _chat(ADMIN_CHAT)
    blocked = client.post("/admin/block",
                          json={"participant_id": target, "blocked": True,
                                "reason": "нет доступа к деньгам"}, headers=headers)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert _row(target)["status"] == "blocked"

    chat_after = client.get("/tools/me/capabilities", headers=_chat(784200))
    assert chat_after.status_code == 403, "чат закрывается сразу, без перезапуска"
    assert "participant_inactive" in chat_after.text
    webapp_after = client.get("/webapp/my", headers=webapp)
    assert webapp_after.status_code == 403, "Mini App закрывается тем же статусом"
    assert "participant_blocked" in webapp_after.text

    assert "participant.block" in _audit(target)

    back = client.post("/admin/block",
                       json={"participant_id": target, "blocked": False}, headers=headers).json()
    assert back["status"] == "active", "возврат идёт в прежний статус, а не наугад"
    assert _row(target)["status"] == "active"
    assert client.get("/tools/me/capabilities", headers=_chat(784200)).status_code == 200


def test_блокировка_из_панели_идёт_тем_же_путём(client):
    """FR-A9: кнопка Mini App и кнопка чата — одна функция и одно состояние."""
    _api()
    admin = _admin()
    target = _person(783200, "Блокировка Из Панели", role="troupe", status="active")
    webapp_admin = _webapp(client, ADMIN_CHAT)
    assert admin

    r = client.post("/webapp/admin/block",
                    json={"participant_id": target, "blocked": True, "reason": "webapp"},
                    headers=webapp_admin)
    assert r.status_code == 200, r.text
    assert _row(target)["status"] == "blocked"
    assert "participant.block" in _audit(target)

    # without participants.manage — отказ, состояние не меняется
    plain = _person(783201, "Блокировка Без Прав", role="troupe", status="active")
    other = _webapp(client, 783201)
    denied = client.post("/webapp/admin/block",
                         json={"participant_id": plain, "blocked": True}, headers=other)
    assert denied.status_code == 403
    assert _row(plain)["status"] == "active"


def test_блокировка_себя_запрещена(client):
    """FR-A9: блокировка себя закрыла бы панель — отказ на сервере."""
    _api()
    admin = _admin()
    r = client.post("/admin/block", json={"participant_id": admin, "blocked": True},
                    headers=_chat(ADMIN_CHAT))
    assert r.status_code == 400
    assert "self_block" in r.text


def test_разблокировать_незаблокированного_нельзя(client):
    """FR-A9: второй рубеж — состояние проверяется по факту, а не по кнопке."""
    _api()
    _admin()
    target = _person(784202, "Не Блокирован", role="troupe", status="active")
    r = client.post("/admin/block", json={"participant_id": target, "blocked": False},
                    headers=_chat(ADMIN_CHAT))
    assert r.status_code == 400
    assert "not_blocked" in r.text


# ------------------------------------------------------------- Л6: архив и возврат
def test_архив_из_чата_и_с_экрана_дают_один_результат(client):
    """FR-A7: маршрут чата и маршрут экрана — одна функция и одно последствие."""
    _api()
    _admin()
    from_chat = _person(784300, "Архив Из Чата", role="troupe", status="active")
    from_webapp = _person(784301, "Архив С Экрана", role="troupe", status="active")
    chat = _chat(ADMIN_CHAT)
    webapp = _webapp(client, ADMIN_CHAT)

    assert client.post("/admin/archive", json={"participant_id": from_chat, "reason": "chat"},
                       headers=chat).status_code == 200
    assert client.post("/webapp/admin/archive",
                       json={"participant_id": from_webapp, "reason": "webapp"},
                       headers=webapp).status_code == 200

    assert _row(from_chat)["status"] == _row(from_webapp)["status"] == "archived"
    assert _audit(from_chat).count("participant.archive") == 1
    assert _audit(from_webapp).count("participant.archive") == 1

    # возврат — перекрёстно: из чата то, что архивировал экран, и наоборот
    assert client.post("/admin/unarchive",
                       json={"participant_id": from_webapp, "reason": "chat"},
                       headers=chat).status_code == 200
    assert client.post("/webapp/admin/unarchive",
                       json={"participant_id": from_chat, "reason": "webapp"},
                       headers=webapp).status_code == 200

    assert _row(from_chat)["status"] == _row(from_webapp)["status"] == "active"
    assert _audit(from_chat).count("participant.unarchive") == 1
    assert _audit(from_webapp).count("participant.unarchive") == 1


def test_отозванный_админ_не_получает_алерты(client):
    """FR-A6: отзыв роли действует и там, где читают `participant_role` напрямую.

    Раньше отзыв удалял строку, поэтому такие запросы были верны. Теперь строка живёт
    с пометкой `revoked_at`, и без фильтра отозванный админ продолжал бы получать
    аварийные алерты (`scripts/alerts.py`, список получателей).
    """
    _api()
    _admin()
    from scripts.alerts import admin_ids

    target = _person(784400, "Отозванный Админ", role="troupe", status="active")
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into participant_role (participant_id, role) "
                     "values (%s, 'admin')", (target,))
        conn.execute("update participant set role = 'admin' where id = %s", (target,))
        conn.commit()

    with psycopg.connect(_test_dsn()) as conn:
        assert target in admin_ids(conn)

    client.post("/admin/grant",
                json={"participant_id": target, "role": "admin", "enabled": False},
                headers=_chat(ADMIN_CHAT))

    with psycopg.connect(_test_dsn()) as conn:
        assert target not in admin_ids(conn), "отозванный админ не получает алерты"

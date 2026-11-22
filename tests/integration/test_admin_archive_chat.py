"""Архив человека из чата: второй рубеж — сервер проверяет право.

Карта `t_6f32dc2f` (AUD-3), реестр `docs/architecture/25` §9.4 строка Л6,
требование FR-A7. Оболочка чата (`panel:archive`, `panel:unarchive:*`) зовёт
`POST /admin/archive` и `/admin/unarchive`; это те же функции, что у экрана
(`/webapp/admin/archive`, `/webapp/admin/unarchive`), — один путь записи.

Проверяем именно отказ: организатор без `participants.manage` получает 403,
даже если кнопка осталась от старого меню.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn

ADMIN_CHAT = 123456789
ORG_HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(ADMIN_CHAT)}


def _person(name: str, status: str = "active") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (display_name, person_key, role, status) "
            "values (%s, %s, 'troupe', %s) returning id",
            (name, make_key(name), status)).fetchone()[0]
        conn.commit()
    return pid


def _grant_manage() -> int:
    """Точечная выдача participants.manage организатору фикстуры — и его id."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()[0]
        conn.execute(
            "insert into participant_capability_override "
            "(participant_id, capability, allowed, reason) values (%s, %s, true, %s) "
            "on conflict (participant_id, capability) do update set allowed = true",
            (pid, "participants.manage", "тест AUD-3"))
        conn.commit()
    return pid


def _unbound(name: str) -> int:
    """Человек без Telegram — на него выписывается персональный код."""
    return _person(name)


def _admin_session(client) -> dict:
    """Сессия Mini App администратора: код выпускается только оттуда (§4.3.3)."""
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) "
                     "values (%s, 'admin') on conflict do nothing", (pid,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(ADMIN_CHAT)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_архив_из_чата_отказывает_без_права(client) -> None:
    """FR-A7: организатор без participants.manage — 403, а не тихая запись."""
    pid = _person("Архив Без Прав")
    response = client.post("/admin/archive",
                           json={"participant_id": pid, "reason": "chat"}, headers=ORG_HEADERS)
    assert response.status_code == 403, response.text

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from participant where id = %s", (pid,)).fetchone()[0]
    assert status == "active", "отказ не должен менять данные"


def test_архив_и_возврат_из_чата_пишут_через_один_путь(client) -> None:
    """FR-A7: с правом — архивируем и возвращаем теми же функциями, что у экрана."""
    _grant_manage()
    pid = _person("Архив Из Чата")

    archived = client.post("/admin/archive",
                           json={"participant_id": pid, "reason": "chat"}, headers=ORG_HEADERS)
    assert archived.status_code == 200, archived.text
    assert archived.json()["name"] == "Архив Из Чата"

    listed = client.get("/tools/participants?include_archived=true", headers=ORG_HEADERS).json()
    row = next(item for item in listed["data"] if item["id"] == pid)
    assert row["status"] == "archived"

    back = client.post("/admin/unarchive",
                       json={"participant_id": pid, "reason": "chat"}, headers=ORG_HEADERS)
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "active"


def test_себя_в_архив_нельзя(client) -> None:
    """FR-A7: правило то же, что на экране, — оболочка его не обходит."""
    me = _grant_manage()
    response = client.post("/admin/archive",
                           json={"participant_id": me, "reason": "chat"}, headers=ORG_HEADERS)
    assert response.status_code == 400, response.text


def test_отзыв_кода_отказывает_без_права(client) -> None:
    """FR-A2: без registration.code.issue отзыв кода — 403, а код остаётся живым."""
    person = _unbound("Отзыв Получатель")
    code = client.post("/webapp/admin/invite", json={"participant_id": person},
                       headers=_admin_session(client)).json()["code"]

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into participant (tg_chat_id, display_name, person_key, "
                     "role, status) values (%s, %s, %s, 'troupe', 'active')",
                     (785100, "Отзыв Без Прав", make_key("Отзыв Без Прав")))
        conn.commit()
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    token = client.post("/webapp/auth", json={"init_data": init_data(785100)}).json()["token"]
    stranger = {"Authorization": f"Bearer {token}"}

    response = client.post("/webapp/admin/invite-revoke", json={"code": code},
                           headers=stranger)
    assert response.status_code == 403, response.text

    alive = client.get("/webapp/admin/invites", headers=_admin_session(client)).json()["data"]
    assert code in [item["code"] for item in alive], "отказ не должен гасить код"

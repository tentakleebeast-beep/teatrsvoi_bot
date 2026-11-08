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
    code = client.post("/admin/invite", json={"role": "troupe", "ttl_days": 7},
                       headers=ORG_HEADERS).json()["code"]

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into participant (tg_chat_id, display_name, person_key, "
                     "role, status) values (%s, %s, %s, 'troupe', 'active')",
                     (785100, "Отзыв Без Прав", make_key("Отзыв Без Прав")))
        conn.commit()
    stranger = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "785100"}

    response = client.post("/admin/invite/revoke", json={"code": code}, headers=stranger)
    assert response.status_code == 403, response.text

    alive = client.get("/admin/invites", headers=ORG_HEADERS).json()["data"]
    assert code in [item["code"] for item in alive], "отказ не должен гасить код"

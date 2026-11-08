"""
Приглашения в админ-панели: код выпускается, виден и отзывается кнопками.

Раньше единственным способом было напечатать `/invite org` в чате. Теперь код
создаётся в панели Mini App теми же обработчиками, что и раньше, и с тем же
правом `registration.code.issue`; список открытых кодов виден рядом.

Правила и требования раздела: docs/development/73-admin-panel-webapp.md.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_код_выпускается_и_виден_в_списке(client, auth) -> None:
    created = client.post("/admin/invite", json={"role": "troupe", "ttl_days": 7},
                          headers=auth).json()
    assert created["ok"] is True and created["code"]

    listed = client.get("/admin/invites", headers=auth).json()
    codes = [item["code"] for item in listed["data"]]
    assert created["code"] in codes
    item = next(i for i in listed["data"] if i["code"] == created["code"])
    assert item["role"] == "troupe" and item["uses"] == 0 and item["max_uses"] == 1


def test_отозванный_код_исчезает_из_списка_и_не_впускает(client, auth) -> None:
    code = client.post("/admin/invite", json={"role": "org"}, headers=auth).json()["code"]
    revoked = client.post("/admin/invite/revoke", json={"code": code}, headers=auth).json()
    assert revoked["ok"] is True

    listed = client.get("/admin/invites", headers=auth).json()
    assert code not in [item["code"] for item in listed["data"]]

    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 785001, "code": code}).json()
    assert check["ok"] is False, "отозванный код не должен пускать в бота"


def test_повторный_отзыв_не_молчит(client, auth) -> None:
    """Уже отозванный код — это ошибка, а не тихий «ок»: иначе непонятно, сработало ли."""
    code = client.post("/admin/invite", json={"role": "troupe"}, headers=auth).json()["code"]
    client.post("/admin/invite/revoke", json={"code": code}, headers=auth)
    again = client.post("/admin/invite/revoke", json={"code": code}, headers=auth)
    assert again.status_code == 404


def test_без_права_список_кодов_закрыт(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 785002, "Коды Без Прав")
    response = client.get("/admin/invites", headers={
        "Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "785002"})
    assert response.status_code == 403


def test_в_панели_код_выпускается_кнопкой(client) -> None:
    """Mini App: тот же выпуск, но через подпись initData и право из БД."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 785010, "Коды Админ", role="admin")
    headers = _token(client, 785010)

    created = client.post("/webapp/admin/invite", json={"role": "troupe"}, headers=headers)
    assert created.status_code == 200 and created.json()["code"]

    listed = client.get("/webapp/admin/invites", headers=headers).json()
    assert created.json()["code"] in [item["code"] for item in listed["data"]]

    revoked = client.post("/webapp/admin/invite-revoke",
                          json={"code": created.json()["code"]}, headers=headers)
    assert revoked.status_code == 200

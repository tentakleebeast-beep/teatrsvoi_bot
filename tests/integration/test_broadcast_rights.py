"""FR-G2, П-6: рассылка — право `notify.send`, и оно только у администратора (Ш-02).

До правки роль `org` держала `notify.send` (`db/init/008`), поэтому организатор видел
строку «Рассылка» в «Управлении» и мог отправить рассылку. Решение владельца П-6
(`docs/reports/124` §1): действие остаётся под правом `notify.send`, но выдаётся только
админу; миграция `057_broadcast_admin_only.sql` снимает связь org → notify.send.

Обоснование: FR-G2, docs/development/92 §2.4 (матрица ролей), docs/reports/124 §1 П-6.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str) -> int:
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


def test_организатору_рассылка_закрыта(client):
    """Организатор: нет строки «Рассылка», нет данных и нет отправки."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783001, "Рассылка Орг", "org")

    org = _token(client, 783001)

    home = client.get("/webapp/home/data", headers=org)
    assert home.status_code == 200, home.text
    urls = {row["url"] for row in home.json()["more"]["management"]}
    assert "/webapp/broadcast" not in urls, "организатор видит вход в рассылку"

    preview = client.get("/webapp/admin/broadcast/preview", headers=org)
    assert preview.status_code == 403, preview.text
    assert "notify.send" in preview.json()["detail"]

    sent = client.post("/webapp/admin/broadcast", headers=org, json={"text": "привет"})
    assert sent.status_code == 403, sent.text
    assert "notify.send" in sent.json()["detail"]


def test_админу_рассылка_доступна(client):
    """Админ видит строку и получает предпросмотр — право не сломали вместе с ролью."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 783002, "Рассылка Админ", "admin")

    admin = _token(client, 783002)

    home = client.get("/webapp/home/data", headers=admin)
    assert home.status_code == 200, home.text
    urls = {row["url"] for row in home.json()["more"]["management"]}
    assert "/webapp/broadcast" in urls

    preview = client.get("/webapp/admin/broadcast/preview", headers=admin)
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] is True
    assert preview.json()["total"] >= 1

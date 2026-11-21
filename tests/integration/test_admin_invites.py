"""Приглашения в админ-панели: код выпускается, виден и отзывается кнопками.

Раньше единственным способом было напечатать `/invite org` в чате. Теперь код
создаётся в панели Mini App теми же обработчиками, что и раньше, и с тем же
правом `registration.code.issue`; список открытых кодов виден рядом.

У выпуска из Mini App есть и вторая половина: код остаётся на экране, а
инструкция получателю уходит в чат уведомлением (`docs/development/179`).

Правила и требования раздела: docs/development/73-admin-panel-webapp.md.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from services.shared.invite import INVITE_LINK
from services.shared.onboarding import INVITE_INSTRUCTION
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

def test_код_педагога_выпускается_из_mini_app(client, auth) -> None:
    """Роль `master` — такая же кнопка, как труппа и организатор (карта E10, дефект L-049).

    Дефект: тело `POST /webapp/admin/invite` объявляло `Literal["org", "troupe"]`, поэтому
    третий сегмент экрана отдавал 422 «Unprocessable Entity». Бот тот же код выпускал
    (`InviteIn` знал `master`), а стенд Mini App отвечал успехом — расхождение живого
    API со стендом и не заметили.
    """
    session = _token(client, 123456789)          # сессия Mini App, не токен бота
    created = client.post("/webapp/admin/invite", json={"role": "master", "ttl_days": 7},
                          headers=session)
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "master"

    listed = client.get("/webapp/admin/invites", headers=session).json()
    assert created.json()["code"] in [item["code"] for item in listed["data"]]


def test_мини_app_и_бот_принимают_один_набор_ролей() -> None:
    """Сторож расхождения: стенд и API не должны знать разные наборы ролей.

    Сравниваются сами модели, а не экран: экран рисует кнопки, а отказывает модель.
    """
    from typing import get_args

    from app.api.admin import InviteIn, WebAppInviteIn

    bot_roles = set(get_args(InviteIn.model_fields["role"].annotation))
    app_roles = set(get_args(WebAppInviteIn.model_fields["role"].annotation))
    assert app_roles == bot_roles - {"admin"}, (
        f"Mini App принимает {sorted(app_roles)}, бот {sorted(bot_roles)}: "
        "роль, добавленная в один вход, обязана появиться и в другом")


def _notes(recipient_id: int) -> list[dict]:
    """Записи очереди этого адресата — то, что увидит отправитель (docs/179 §5)."""
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute(
            "select id, kind, recipient_id, payload, dedupe_key, status, scheduled_at "
            "from notification where recipient_id = %s order by id", (recipient_id,)).fetchall()


def test_выпуск_из_mini_app_ставит_инструкцию_в_очередь(client) -> None:
    """E12 (docs/development/179 §5.1): код на экране, инструкция получателю — в чат.

    Адресат — тот, кто выпустил: инструкцию он пересылает человеку, которому отдаёт
    код. Текста кода в сообщении нет — код виден только на экране шторки.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785020, "Инструкция Выпустил", role="admin")
    headers = _token(client, 785020)

    created = client.post("/webapp/admin/invite", json={"role": "troupe"},
                          headers=headers).json()
    code = created["code"]

    notes = _notes(pid)
    assert len(notes) == 1, f"ожидалась одна строка очереди, пришло {len(notes)}"
    note = notes[0]
    assert note["kind"] == "invite_instruction"
    assert note["recipient_id"] == pid
    assert note["status"] == "scheduled" and note["scheduled_at"] is not None
    assert note["payload"]["title"] == INVITE_LINK
    assert note["payload"]["body"] == INVITE_INSTRUCTION
    assert code not in note["payload"]["title"]
    assert code not in note["payload"]["body"]


def test_выпуск_из_бота_инструкцию_в_чат_не_ставит(client, auth) -> None:
    """E12 (docs/development/179 §5.2): бот присылает код и инструкцию одним сообщением.

    Оба входа сходятся в `create_invite`, поэтому уведомление ставится только в
    `webapp_admin_invite`: иначе админ получил бы инструкцию дважды.
    """
    _api()
    created = client.post("/admin/invite", json={"role": "troupe"}, headers=auth)
    assert created.status_code == 200, created.text
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = conn.execute("select id from notification where kind = 'invite_instruction'"
                            ).fetchall()
    assert rows == [], "бот-путь не должен ставить инструкцию в очередь"


def test_повторное_нажатие_не_плодит_инструкцию(client) -> None:
    """E12 (docs/development/179 §5.3, правило Д4): второй код — та же инструкция.

    Ключ по коду от двойного нажатия не спасает (каждое нажатие выпускает новый код),
    поэтому вторую строку отсекает правило «не чаще одной инструкции в сутки».
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785021, "Инструкция Дважды", role="admin")
    headers = _token(client, 785021)

    first = client.post("/webapp/admin/invite", json={"role": "troupe"}, headers=headers).json()
    second = client.post("/webapp/admin/invite", json={"role": "troupe"}, headers=headers).json()
    assert first["code"] != second["code"], "каждое нажатие выпускает новый код"

    codes = [item["code"] for item in
             client.get("/webapp/admin/invites", headers=headers).json()["data"]]
    assert {first["code"], second["code"]} <= set(codes), "оба кода остаются рабочими"

    assert len(_notes(pid)) == 1, "второй код не должен давать второе сообщение"


def test_вчерашняя_инструкция_не_мешает_новой(client) -> None:
    """E12 (docs/development/179 §Д4): окно ограничения — сутки, а не навсегда.

    Иначе админ, выпускавший код вчера, не получил бы инструкцию сегодня: текст у
    него в чате, но человеку он его уже отдал.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785022, "Инструкция Вчера", role="admin")
    headers = _token(client, 785022)
    client.post("/webapp/admin/invite", json={"role": "troupe"}, headers=headers)

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update notification set status = 'sent', sent_at = now() - interval '25 hours' "
                     "where recipient_id = %s and kind = 'invite_instruction'", (pid,))
        conn.commit()

    client.post("/webapp/admin/invite", json={"role": "troupe"}, headers=headers)
    assert len(_notes(pid)) == 2, "старше суток — новое сообщение ставится"

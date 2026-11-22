"""Приглашения: персональный код на человека, список, отзыв, инструкция в чат.

Код выписывается на конкретного человека, у которого ещё нет Telegram
(docs/development/182 §1.1, §4.3.4, V-116 б). Роль берётся из записи, а не из
тела запроса: выбора роли при выпуске больше нет. Экран Mini App — единственный
вход, из чата коды не выпускаются (V-118 а, §4.3.3).

Вторая половина выпуска — инструкция получателю: код остаётся на экране, а
текст уходит уведомлением тому, кто выпустил (docs/development/179).

Обоснование: FR-A2 (код приглашения), FR-A5 (роли), docs/development/182 §4.3.4,
docs/development/179 §5.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from services.shared.invite import INVITE_LINK
from services.shared.onboarding import INVITE_INSTRUCTION
from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int | None, name: str, role: str = "troupe",
            status: str = "active") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,%s) returning id",
        (chat_id, name, make_key(name), role, status)).fetchone()["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    """Сессия администратора из фикстуры плюс человек без Telegram для кода."""
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        admin_id = conn.execute("select id from participant where tg_chat_id = %s",
                                (123456789,)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin') "
                     "on conflict do nothing", (admin_id,))
        conn.commit()
        person = _person(conn, None, "Кодов Получатель")
    return {"headers": _token(client, 123456789), "person": person}


def test_код_выпускается_на_человека_и_виден_в_списке(client) -> None:
    setup = _admin(client)
    created = client.post("/webapp/admin/invite", json={"participant_id": setup["person"]},
                          headers=setup["headers"])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["ok"] is True and body["code"]
    assert body["role"] == "troupe"
    assert body["participant_id"] == setup["person"]
    assert body["display_name"] == "Кодов Получатель"

    listed = client.get("/webapp/admin/invites", headers=setup["headers"]).json()
    item = next(i for i in listed["data"] if i["code"] == body["code"])
    assert item["role"] == "troupe" and item["uses"] == 0 and item["max_uses"] == 1
    assert item["participant_id"] == setup["person"]
    assert item["display_name"] == "Кодов Получатель"


def test_код_на_привязанного_человека_не_выпускается(client) -> None:
    """Персональный код — вход для того, у кого Telegram ещё нет (§1.1)."""
    setup = _admin(client)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        bound = _person(conn, 785101, "Уже В Боте")
    response = client.post("/webapp/admin/invite", json={"participant_id": bound},
                           headers=setup["headers"])
    assert response.status_code == 400
    assert "participant_already_bound" in response.text


def test_код_на_архивного_человека_не_выпускается(client) -> None:
    setup = _admin(client)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        archived = _person(conn, None, "Архивный Человек", status="archived")
    response = client.post("/webapp/admin/invite", json={"participant_id": archived},
                           headers=setup["headers"])
    assert response.status_code == 400
    assert "participant_not_available" in response.text


def test_код_на_несуществующего_человека_не_выпускается(client) -> None:
    setup = _admin(client)
    response = client.post("/webapp/admin/invite", json={"participant_id": 999999},
                           headers=setup["headers"])
    assert response.status_code == 404
    assert "participant_not_found" in response.text


def test_код_дминистратору_не_выпускается(client) -> None:
    setup = _admin(client)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        admin_id = _person(conn, None, "Второй Админ", role="admin")
    response = client.post("/webapp/admin/invite", json={"participant_id": admin_id},
                           headers=setup["headers"])
    assert response.status_code == 400
    assert "ADMIN_CHAT_ID" in response.text


def test_роль_в_теле_игнорируется(client) -> None:
    """Роль берётся из записи: поля `role` в теле выпуска больше нет (§4.3.2)."""
    setup = _admin(client)
    created = client.post("/webapp/admin/invite",
                          json={"participant_id": setup["person"], "role": "org"},
                          headers=setup["headers"])
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "troupe"


def test_отозванный_код_исчезает_из_списка_и_не_впускает(client) -> None:
    setup = _admin(client)
    code = client.post("/webapp/admin/invite", json={"participant_id": setup["person"]},
                       headers=setup["headers"]).json()["code"]
    revoked = client.post("/webapp/admin/invite-revoke", json={"code": code},
                          headers=setup["headers"])
    assert revoked.status_code == 200

    listed = client.get("/webapp/admin/invites", headers=setup["headers"]).json()
    assert code not in [item["code"] for item in listed["data"]]

    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 785001, "code": code}).json()
    assert check["ok"] is False, "отозванный код не должен пускать в бота"


def test_повторный_отзыв_не_молчит(client) -> None:
    """Уже отозванный код — это ошибка, а не тихий «ок»: иначе непонятно, сработало ли."""
    setup = _admin(client)
    code = client.post("/webapp/admin/invite", json={"participant_id": setup["person"]},
                       headers=setup["headers"]).json()["code"]
    client.post("/webapp/admin/invite-revoke", json={"code": code}, headers=setup["headers"])
    again = client.post("/webapp/admin/invite-revoke", json={"code": code},
                        headers=setup["headers"])
    assert again.status_code == 404


def test_без_права_список_кодов_закрыт(client) -> None:
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        _person(conn, 785002, "Коды Без Прав")
    response = client.get("/webapp/admin/invites", headers=_token(client, 785002))
    assert response.status_code == 403


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
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        pid = _person(conn, 785020, "Инструкция Выпустил", role="admin")
        person = _person(conn, None, "Инструкция Получатель")
    headers = _token(client, 785020)

    created = client.post("/webapp/admin/invite", json={"participant_id": person},
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


def test_повторное_нажатие_не_плодит_инструкцию(client) -> None:
    """E12 (docs/development/179 §5.3, правило Д4): второй код — та же инструкция.

    Ключ по коду от двойного нажатия не спасает (каждое нажатие выпускает новый код),
    поэтому вторую строку отсекает правило «не чаще одной инструкции в сутки».
    """
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        pid = _person(conn, 785021, "Инструкция Дважды", role="admin")
        first_person = _person(conn, None, "Дважды Первый")
        second_person = _person(conn, None, "Дважды Второй")
    headers = _token(client, 785021)

    first = client.post("/webapp/admin/invite", json={"participant_id": first_person},
                        headers=headers).json()
    second = client.post("/webapp/admin/invite", json={"participant_id": second_person},
                         headers=headers).json()
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
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        pid = _person(conn, 785022, "Инструкция Вчера", role="admin")
        person = _person(conn, None, "Вчера Получатель")
    headers = _token(client, 785022)
    client.post("/webapp/admin/invite", json={"participant_id": person}, headers=headers)

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update notification set status = 'sent', sent_at = now() - interval '25 hours' "
                     "where recipient_id = %s and kind = 'invite_instruction'", (pid,))
        conn.commit()

    client.post("/webapp/admin/invite", json={"participant_id": person}, headers=headers)
    assert len(_notes(pid)) == 2, "старше суток — новое сообщение ставится"

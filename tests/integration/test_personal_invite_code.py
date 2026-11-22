"""Персональный код приглашения: выпуск, проверка, привязка (docs/development/182 §1.4).

Код выписывается на конкретного человека (V-116 б), поэтому `check_code` отдаёт
имя и роль этого человека, а не список кандидатов; `attach` привязывает Telegram
к записи кода и ставит `active`. Заявок нет: ветка `pending` вместе с `register`
удалена (V-117, V-118).

Обоснование: FR-A2 (код приглашения), FR-A5 (роли), FR-G16 (первый вход),
docs/development/182 §1.3, §1.4, §4.3.4, §6.2 п. 6.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from services.shared import onboarding
from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(chat_id: int | None, name: str, role: str = "troupe",
            status: str = "active") -> int:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,%s,%s) returning id",
            (chat_id, name, make_key(name), role, status)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                     (pid, role))
        conn.commit()
    return pid


def _issue(person_id: int | None, code: str = "PERSONA1", *, role: str = "troupe",
           expired: bool = False, uses: int = 0, max_uses: int = 1) -> str:
    """Кладём строку кода прямо в схему: так проверяется поведение, а не выпуск."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into invite_code (code, role, participant_id, created_by, expires_at,
                                        max_uses, uses)
               values (%s, %s, %s, 123456789,
                       now() - case when %s then interval '1 day' else interval '-7 days' end,
                       %s, %s)""",
            (code, role, person_id, expired, max_uses, uses))
    return code


def _admin_headers(client) -> dict:
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        admin_id = conn.execute("select id from participant where tg_chat_id = %s",
                                (123456789,)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin') "
                     "on conflict do nothing", (admin_id,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(123456789)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# --- выпуск ----------------------------------------------------------------


def test_выпуск_пишет_человека_и_роль_из_записи(client) -> None:
    headers = _admin_headers(client)
    person = _person(None, "Кодов Получатель", role="org")
    created = client.post("/webapp/admin/invite", json={"participant_id": person},
                          headers=headers)
    assert created.status_code == 200, created.text
    body = created.json()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        row = conn.execute("select role, participant_id, max_uses, uses "
                           "from invite_code where code = %s", (body["code"],)).fetchone()
    assert row["role"] == "org" and row["participant_id"] == person
    assert row["max_uses"] == 1 and row["uses"] == 0


def test_max_uses_в_теле_игнорируется_код_одноразовый(client) -> None:
    headers = _admin_headers(client)
    person = _person(None, "Одноразовый Человек")
    created = client.post("/webapp/admin/invite",
                          json={"participant_id": person, "max_uses": 5},
                          headers=headers)
    assert created.status_code == 200, created.text
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        row = conn.execute("select max_uses from invite_code where code = %s",
                           (created.json()["code"],)).fetchone()
    assert row["max_uses"] == 1


def test_выпуск_на_привязанного_человека_отказ(client) -> None:
    headers = _admin_headers(client)
    bound = _person(790001, "Уже В Боте")
    response = client.post("/webapp/admin/invite", json={"participant_id": bound},
                           headers=headers)
    assert response.status_code == 400
    assert "participant_already_bound" in response.text


def test_выпуск_на_архивного_и_блокированного_отказ(client) -> None:
    headers = _admin_headers(client)
    for name, status in (("Архивный", "archived"), ("Блокированный", "blocked")):
        person = _person(None, name, status=status)
        response = client.post("/webapp/admin/invite", json={"participant_id": person},
                               headers=headers)
        assert response.status_code == 400, response.text
        assert "participant_not_available" in response.text


def test_выпуск_администратору_отказ(client) -> None:
    headers = _admin_headers(client)
    person = _person(None, "Новый Админ", role="admin")
    response = client.post("/webapp/admin/invite", json={"participant_id": person},
                           headers=headers)
    assert response.status_code == 400
    assert "ADMIN_CHAT_ID" in response.text


# --- проверка кода ---------------------------------------------------------


def test_check_code_отдаёт_имя_и_не_отдаёт_кандидатов(client, auth) -> None:
    person = _person(None, "Сидоров Иван")
    code = _issue(person)
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 791001, "code": code}, headers=auth).json()
    assert body["ok"] is True
    assert body["participant"]["display_name"] == "Сидоров Иван"
    assert body["role"] == "troupe"
    assert "candidates" not in body


def test_check_code_без_человека_не_пускает(client, auth) -> None:
    """Старый код «на роль» (participant_id is null) больше не принимается."""
    code = _issue(None)
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 791002, "code": code}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "code_not_personal"


def test_check_code_блокированного_человека(client, auth) -> None:
    person = _person(None, "Заблокированный Получатель", status="blocked")
    code = _issue(person)
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 791003, "code": code}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "participant_blocked"


def test_check_code_истёкший_и_использованный(client, auth) -> None:
    person = _person(None, "Просроченный Получатель")
    old = _issue(person, "EXPIRED1", expired=True)
    used = _issue(_person(None, "Уже Вошёл"), "USEDCODE", uses=1)
    for code, error in ((old, "code_expired"), (used, "code_exhausted")):
        body = client.post("/tools/participants/check_code",
                           json={"chat_id": 791004, "code": code}, headers=auth).json()
        assert body["ok"] is False and body["error"] == error


# --- привязка --------------------------------------------------------------


def test_attach_привязывает_и_ставит_active(client, auth) -> None:
    person = _person(None, "Сидоров Иван", status="pending")
    code = _issue(person)
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 792001, "code": code}, headers=auth).json()
    assert body["ok"] is True
    assert body["participant"]["chat_id"] == 792001
    assert body["participant"]["status"] == "active"
    assert "troupe" in body["participant"]["roles"]


def test_повторный_код_не_привязывает_второй_раз(client, auth) -> None:
    person = _person(None, "Один Раз")
    code = _issue(person, max_uses=3)
    first = client.post("/tools/participants/attach",
                        json={"chat_id": 792002, "code": code}, headers=auth).json()
    assert first["ok"] is True
    second = client.post("/tools/participants/attach",
                         json={"chat_id": 792003, "code": code}, headers=auth).json()
    assert second["ok"] is False
    assert second["error"] == "code_exhausted"
    with psycopg.connect(_test_dsn()) as conn:
        chat_id = conn.execute("select tg_chat_id from participant where id = %s",
                               (person,)).fetchone()[0]
    assert chat_id == 792002


def test_attach_код_без_человека_отказ(client, auth) -> None:
    code = _issue(None, "NOBODY01")
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 792004, "code": code}, headers=auth).json()
    assert body["ok"] is False and body["error"] == "code_not_personal"


def test_attach_блокированного_человека_отказ(client, auth) -> None:
    person = _person(None, "Блок Привязки", status="blocked")
    code = _issue(person, "BLOCKED1")
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 792005, "code": code}, headers=auth).json()
    assert body["ok"] is False and body["error"] == "participant_blocked"


def test_привязка_даёт_возможности_роли(client, auth) -> None:
    """Обращение №24: запись без строки `participant_role` врала «0 возможностей»."""
    person = _person(None, "Смирнова Дарья")
    code = _issue(person)
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 792006, "code": code}, headers=auth).json()
    assert body["ok"] is True
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "792006"}
    me = client.get("/tools/participants/me", headers=headers).json()
    assert me["data"]["roles"] == ["troupe"]
    assert "schedule.availability.self" in me["data"]["capabilities"]


# --- педагог: дисциплина ---------------------------------------------------


def test_педагог_без_дисциплины_просит_выбрать(client, auth) -> None:
    person = _person(None, "Педагог Без Предмета", role="master")
    code = _issue(person, role="master")
    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 793001, "code": code}, headers=auth).json()
    assert check["ok"] is True and check["needs_discipline"] is True

    attached = client.post("/tools/participants/attach",
                           json={"chat_id": 793001, "code": code}, headers=auth).json()
    assert attached["ok"] is False and attached["error"] == "need_discipline"


def test_педагог_с_дисциплиной_привязывается_и_пишет_связь(client, auth) -> None:
    person = _person(None, "Педагог С Предметом", role="master")
    code = _issue(person, role="master")
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 793002, "code": code, "discipline_code": "music"},
                       headers=auth).json()
    assert body["ok"] is True
    assert body["participant"]["role"] == "master"
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            """select d.code, pd.relation from participant_discipline pd
                 join discipline d on d.id = pd.discipline_id
                where pd.participant_id = %s""", (person,)).fetchone()
    assert row is not None, "дисциплина должна быть записана при привязке"
    assert row[0] == "music" and row[1] == "teacher"


# --- снятые пути -----------------------------------------------------------


def test_register_и_search_удалены(client, auth) -> None:
    """Служебные пути старой регистрации отвечают 404: их больше нет в контракте."""
    register = client.post("/tools/participants/register",
                           json={"chat_id": 794001, "code": "X",
                                 "display_name": "Новиков Пётр"}, headers=auth)
    search = client.get("/tools/participants/search", params={"q": "петр"}, headers=auth)
    assert register.status_code == 404
    assert search.status_code == 404


def test_инструкция_не_в_чате_а_в_общем_модуле() -> None:
    """Текст инструкции один: `services/shared/onboarding`, без дубля в `invite`."""
    assert onboarding.INVITE_INSTRUCTION
    import services.shared.invite as invite_module

    assert not hasattr(invite_module, "INVITE_INSTRUCTION")

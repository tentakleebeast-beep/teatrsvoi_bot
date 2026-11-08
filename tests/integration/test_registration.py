"""UC-07/08: коды приглашения и регистрация (docs/development/28).

Проверяем выпуск кодов (только admin/org/troupe), проверку кода,
привязку chat_id к существующему человеку и защиту от перезаписи.
"""

from __future__ import annotations

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import _test_dsn


def _invite(client, headers, **over):
    payload = {"role": "org", "ttl_days": 7, "max_uses": 1}
    payload.update(over)
    return client.post("/admin/invite", json=payload, headers=headers)


def _insert_code(code: str, role: str = "troupe", expired: bool = False,
                 max_uses: int = 1, uses: int = 0) -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into invite_code (code, role, created_by, expires_at, max_uses, uses)
               values (%s, %s, 123456789,
                       now() - case when %s then interval '1 day' else interval '-7 days' end,
                       %s, %s)""",
            (code, role, expired, max_uses, uses))


# --- UC-07: выпуск кода -----------------------------------------------------

def test_код_выпускается_для_org_и_troupe(client, auth):
    for role in ("org", "troupe"):
        r = _invite(client, auth, role=role)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["role"] == role
        assert len(body["code"]) == 8
        assert body["expires_at"]


def test_код_для_admin_запрещён(client, auth):
    r = _invite(client, auth, role="admin")
    assert r.status_code == 400
    assert "ADMIN_CHAT_ID" in r.json()["detail"]


def test_код_для_student_невалиден(client, auth):
    r = _invite(client, auth, role="student")
    assert r.status_code == 422


def test_код_требует_права_выпуска(client, auth):
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (710001, 'Студиец', 'troupe', 'active')""")
        conn.execute(
            """insert into participant_role (participant_id, role)
               select id, 'troupe' from participant where tg_chat_id = 710001""")
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "710001"}
    r = _invite(client, headers)
    assert r.status_code == 403
    assert "registration.code.issue" in r.json()["detail"]


# --- UC-08: проверка кода ---------------------------------------------------

def test_check_code_валидный_код(client, auth):
    _insert_code("VALID234", role="troupe")
    r = client.post("/tools/participants/check_code",
                    json={"chat_id": 720001, "code": "valid234"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["role"] == "troupe"
    assert isinstance(body["candidates"], list)


@pytest.mark.parametrize("code,expired,error", [
    ("EXPIRED2", True, "code_expired"),
    ("NOSUCH99", False, "code_not_found"),
])
def test_check_code_отклоняет_плохой_код(client, auth, code, expired, error):
    """UC-08: проверка кода различает просроченный и несуществующий."""
    if expired:
        _insert_code(code, expired=True)
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 720002, "code": code}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == error


def test_check_code_уже_зарегистрирован(client, auth):
    _insert_code("AGAIN123")
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 123456789, "code": "AGAIN123"}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "already_registered"
    assert body["participant"]["chat_id"] == 123456789


def test_check_code_фильтр_кандидатов(client, auth):
    _insert_code("FILTER12", role="org")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (display_name, role, status)
               values ('Иванов Пётр', 'troupe', 'active')""")
        conn.execute(
            """insert into participant (display_name, role, status)
               values ('Белова Анна', 'troupe', 'active')""")
    body = client.post("/tools/participants/check_code",
                       json={"chat_id": 720004, "code": "FILTER12", "q": "иванов"},
                       headers=auth).json()
    assert body["ok"] is True
    names = [c["display_name"] for c in body["candidates"]]
    assert names == ["Иванов Пётр"]


# --- Ш-46 (V-006): поиск по частям имени ------------------------------------

def _insert_person(name: str, key: str | None = None, status: str = "active",
                   chat_id: int | None = None) -> int:
    key = make_key(name) if key is None else key
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role,
                                        status, phone, birthday)
               values (%s, %s, %s, 'troupe', %s, '+79000000000', '1990-01-01')
               returning id""", (chat_id, name, key, status)).fetchone()[0]


def test_поиск_по_частям_имени_и_фамилии(client, auth):
    """Обращение №26: «Смирнова» должна находить «Дарья Смирнова», а не только точное имя."""
    _insert_person("Дарья Смирнова")
    body = client.get("/tools/participants/search", params={"q": "дарья смир"},
                      headers=auth).json()
    assert body["ok"] is True
    assert [c["display_name"] for c in body["candidates"]] == ["Дарья Смирнова"]


def test_поиск_не_зависит_от_регистра_и_ё(client, auth):
    _insert_person("Пётр Ёлкин")
    body = client.get("/tools/participants/search", params={"q": "ПЕТР елкин"},
                      headers=auth).json()
    assert [c["display_name"] for c in body["candidates"]] == ["Пётр Ёлкин"]


def test_поиск_видит_псевдонимы(client, auth):
    person_id = _insert_person("Виктория Тарасова")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, source)
                        values (%s, 'витя', 'short', 'manual')""", (person_id,))
    body = client.get("/tools/participants/search", params={"q": "витя"},
                      headers=auth).json()
    assert [c["id"] for c in body["candidates"]] == [person_id]


def test_поиск_не_отдаёт_привязанных_и_личное(client, auth):
    """Привязанный человек в список не попадает, телефон и дата рождения — тоже."""
    _insert_person("Занятая Уже", "занятая", chat_id=740001)
    _insert_person("Свободная Дарья")
    body = client.get("/tools/participants/search", params={"q": ""},
                      headers=auth).json()
    names = [c["display_name"] for c in body["candidates"]]
    assert "Занятая Уже" not in names and "Свободная Дарья" in names
    assert set(body["candidates"][0]) == {"id", "display_name", "role"}


def test_поиск_не_привязывает_чужой_профиль(client, auth):
    """Ш-46: поиск только показывает записи — chat_id остаётся пустым."""
    person_id = _insert_person("Дарья Смирнова")
    body = client.get("/tools/participants/search", params={"q": "смирнова"},
                      headers=auth).json()
    assert [c["id"] for c in body["candidates"]] == [person_id]
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        assert conn.execute("select tg_chat_id from participant where id = %s",
                            (person_id,)).fetchone()[0] is None


def test_регистрация_по_имени_не_привязывает_чужой_профиль(client, auth):
    """Совпадение имени — это не привязка: человек сам выбирает запись из списка."""
    person_id = _insert_person("Дарья Смирнова")
    _insert_code("TWINN123", role="troupe")
    body = client.post("/tools/participants/register",
                       json={"chat_id": 740002, "code": "TWINN123",
                             "display_name": "Дарья Смирнова",
                             "require_approval": True}, headers=auth)
    assert body.status_code == 409
    assert body.json()["code"] == "person_key_taken"
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        assert conn.execute("select tg_chat_id from participant where id = %s",
                            (person_id,)).fetchone()[0] is None


# --- UC-08: привязка chat_id ------------------------------------------------

def _insert_unbound(name: str = "Сидоров Иван") -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute(
            """insert into participant (display_name, role, status)
               values (%s, 'troupe', 'pending') returning id""", (name,)).fetchone()[0]


def test_привязка_chat_id_к_существующему(client, auth):
    person_id = _insert_unbound()
    _insert_code("ATTACH12", role="troupe", max_uses=2)
    r = client.post("/tools/participants/attach",
                    json={"chat_id": 730001, "participant_id": person_id,
                          "code": "ATTACH12"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["participant"]["chat_id"] == 730001
    assert body["participant"]["status"] == "active"
    assert "troupe" in body["participant"]["roles"]


def test_повторная_привязка_не_перезаписывает(client, auth):
    person_id = _insert_unbound()
    _insert_code("ATTACH34", role="troupe", max_uses=3)
    first = client.post("/tools/participants/attach",
                        json={"chat_id": 730002, "participant_id": person_id,
                              "code": "ATTACH34"}, headers=auth).json()
    assert first["ok"] is True

    second = client.post("/tools/participants/attach",
                         json={"chat_id": 730003, "participant_id": person_id,
                               "code": "ATTACH34"}, headers=auth).json()
    assert second["ok"] is False
    assert second["error"] == "already_bound"

    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        chat_id = conn.execute(
            "select tg_chat_id from participant where id = %s", (person_id,)).fetchone()[0]
    assert chat_id == 730002


def test_привязка_несуществующего_человека(client, auth):
    _insert_code("ATTACH56", max_uses=2)
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 730004, "participant_id": 999999,
                             "code": "ATTACH56"}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "participant_not_found"


# --- UC-08: регистрация с нуля ---------------------------------------------

def test_регистрация_по_коду_создаёт_участника(client, auth):
    r = _invite(client, auth, role="troupe")
    code = r.json()["code"]
    body = client.post("/tools/participants/register",
                       json={"chat_id": 740001, "code": code,
                             "display_name": "Новиков Пётр"}, headers=auth).json()
    assert body["ok"] is True
    assert body["status"] == "registered"
    assert body["participant"]["role"] == "troupe"
    assert body["participant"]["chat_id"] == 740001


def test_повторная_регистрация_не_создаёт_дубль(client, auth):
    code = _invite(client, auth, role="troupe").json()["code"]
    payload = {"chat_id": 740002, "code": code, "display_name": "Новикова Анна"}
    first = client.post("/tools/participants/register", json=payload, headers=auth).json()
    second = client.post("/tools/participants/register", json=payload, headers=auth).json()
    assert first["ok"] is True and second["ok"] is True
    assert second["status"] == "already_registered"
    assert second["participant"]["id"] == first["participant"]["id"]


def test_регистрация_с_просроченным_кодом(client, auth):
    _insert_code("OLDC0DE1", expired=True)
    body = client.post("/tools/participants/register",
                       json={"chat_id": 740003, "code": "OLDC0DE1",
                             "display_name": "Поздний"}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "code_expired"


# --- №24: регистрация обязана давать возможности роли -----------------------

def test_регистрация_даёт_возможности_роли(client, auth):
    """Обращение №24: запись создавалась без строки `participant_role`, бот врал «0»."""
    code = _invite(client, auth, role="troupe").json()["code"]
    body = client.post("/tools/participants/register",
                       json={"chat_id": 740010, "code": code,
                             "display_name": "Соколов Марк"}, headers=auth).json()
    assert body["ok"] is True
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "740010"}
    me = client.get("/tools/participants/me", headers=headers).json()
    assert me["data"]["roles"] == ["troupe"]
    assert me["data"]["capabilities"], "у role troupe должны быть возможности"
    assert "schedule.availability.self" in me["data"]["capabilities"]


def test_привязка_даёт_возможности_роли(client, auth):
    """Та же причина при привязке chat_id к записи без строк роли (импорт из расписания)."""
    person_id = _insert_unbound("Смирнова Дарья")
    _insert_code("ATTACH78", role="troupe", max_uses=2)
    r = client.post("/tools/participants/attach",
                    json={"chat_id": 730005, "participant_id": person_id,
                          "code": "ATTACH78"}, headers=auth).json()
    assert r["ok"] is True
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "730005"}
    me = client.get("/tools/participants/me", headers=headers).json()
    assert me["data"]["capabilities"], "привязка должна выдать возможности роли"

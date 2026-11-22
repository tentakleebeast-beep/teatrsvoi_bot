"""Педагог входит своим персональным кодом и выбирает дисциплину (064, 15.11.2026).

Решение владельца: `master` — полноценная роль со своим кодом, дисциплину
спрашиваем при онбординге (Актёрское мастерство, Музыка). После V-116 (код
персональный, docs/development/182 §1.4) код выписывается на конкретного
педагога, поэтому «роль кода» больше не выбирается при выпуске.

Обоснование: FR-A2 (регистрация по коду), FR-A5 (четыре роли),
`db/init/064_master_invite.sql`, `docs/architecture/140-master-role.md` §7,
docs/development/182 §1.3, §1.4.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _conn():
    return psycopg.connect(_test_dsn())


def _master(chat_id: int | None = None, name: str = "Педагог Тест") -> int:
    with _conn() as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s, %s, %s, 'master', 'active') returning id",
            (chat_id, name, make_key(name))).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'master')",
                     (pid,))
        conn.commit()
    return pid


def _code(person_id: int | None, code: str) -> str:
    with _conn() as conn:
        conn.execute(
            """insert into invite_code (code, role, participant_id, created_by, expires_at, max_uses)
               values (%s, 'master', %s, 0, now() + interval '7 days', 1)""",
            (code, person_id))
        conn.commit()
    return code


def _admin_session(client) -> dict:
    """Сессия Mini App администратора: код выпускается только оттуда (§4.3.3)."""
    _api()
    with _conn() as conn:
        conn.execute("insert into participant (tg_chat_id, display_name, person_key, "
                     "role, status) values (123456789, 'Код Админ', %s, 'admin', 'active') "
                     "on conflict (tg_chat_id) do nothing", (make_key("Код Админ"),))
        pid = conn.execute("select id from participant where tg_chat_id = 123456789"
                           ).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin') "
                     "on conflict do nothing", (pid,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(123456789)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_код_педагога_выпускается_и_просит_дисциплину(client) -> None:
    person = _master()
    created = client.post("/webapp/admin/invite", json={"participant_id": person},
                          headers=_admin_session(client))
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "master" and created.json()["code"]

    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 991001, "code": created.json()["code"]}).json()
    assert check["ok"] is True
    assert check["needs_discipline"] is True, "бот должен спросить дисциплину до выбора себя"


def test_выпуск_кода_не_пишет_дисциплину(client) -> None:
    """Дисциплина — вопрос онбординга, а не поле выпуска (§4.3.4)."""
    person = _master(name="Педагог Без Предмета")
    created = client.post("/webapp/admin/invite",
                          json={"participant_id": person, "discipline_code": "music"},
                          headers=_admin_session(client))
    assert created.status_code == 200, created.text
    with _conn() as conn:
        row = conn.execute("select discipline_code from invite_code where code = %s",
                           (created.json()["code"],)).fetchone()
    assert row[0] is None


def test_регистрация_педагога_пишет_дисциплину_teacher(client) -> None:
    pid = _master(name="Педагог Музыки")
    code = _code(pid, "PEDAG123")

    attached = client.post("/tools/participants/attach",
                           json={"chat_id": 991002, "code": code,
                                 "discipline_code": "music"}).json()
    assert attached["ok"] is True
    assert attached["participant"]["role"] == "master"

    with _conn() as conn:
        row = conn.execute(
            """select d.code, pd.relation from participant_discipline pd
                 join discipline d on d.id = pd.discipline_id
                where pd.participant_id = %s""", (pid,)).fetchone()
    assert row is not None, "дисциплина должна быть записана при регистрации"
    assert row[0] == "music" and row[1] == "teacher"


def test_неизвестная_дисциплина_не_пускает_и_не_пишет_роль(client) -> None:
    pid = _master(name="Педагог С Опечаткой")
    code = _code(pid, "PEDAG456")

    res = client.post("/tools/participants/attach",
                      json={"chat_id": 991003, "code": code,
                            "discipline_code": "лeтенка"}).json()
    assert res["ok"] is False

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        person = conn.execute("select tg_chat_id, role from participant where id = %s",
                              (pid,)).fetchone()
        links = conn.execute("select count(*) as n from participant_discipline "
                             "where participant_id = %s", (pid,)).fetchone()["n"]
    assert person["tg_chat_id"] is None, "неизвестная дисциплина не должна привязывать"
    assert person["role"] == "master"
    assert links == 0

"""Педагог регистрируется своим кодом и выбирает дисциплину (064, 15.11.2026).

Решение владельца: `master` — полноценная роль со своим инвайт-кодом, дисциплину
спрашиваем при онбординге (Актёрское мастерство, Музыка). До этой правки код
педагога не существовал: роль выдавалась только в карточке человека.
"""

from __future__ import annotations

from app.person_key import make_key

from tests.conftest import _test_dsn


def _conn():
    import psycopg
    return psycopg.connect(_test_dsn())


def test_код_педагога_выпускается_и_просит_дисциплину(client, auth) -> None:
    created = client.post("/admin/invite", json={"role": "master", "ttl_days": 7},
                          headers=auth).json()
    assert created["ok"] is True and created["code"]
    assert created["role"] == "master"

    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 991001, "code": created["code"]}).json()
    assert check["ok"] is True
    assert check["needs_discipline"] is True, "бот должен спросить дисциплину до выбора себя"


def test_дисциплина_к_коду_не_педагога_отклоняется(client, auth) -> None:
    r = client.post("/admin/invite",
                    json={"role": "troupe", "discipline_code": "music"}, headers=auth)
    assert r.status_code == 400


def test_регистрация_педагога_пишет_дисциплину_teacher(client) -> None:
    with _conn() as conn:
        code = conn.execute(
            """insert into invite_code (code, role, created_by, expires_at, max_uses)
               values ('PEDAG123', 'master', 0, now() + interval '7 days', 1) returning code"""
        ).fetchone()[0]
        pid = conn.execute(
            "insert into participant (display_name, person_key, role, status) "
            "values ('Педагог Тест', %s, 'troupe', 'active') returning id",
            (make_key("Педагог Тест"),)).fetchone()[0]
        conn.commit()

    attached = client.post("/tools/participants/attach",
                           json={"chat_id": 991002, "participant_id": pid,
                                 "code": code, "discipline_code": "music"}).json()
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
    with _conn() as conn:
        code = conn.execute(
            """insert into invite_code (code, role, created_by, expires_at, max_uses)
               values ('PEDAG456', 'master', 0, now() + interval '7 days', 1) returning code"""
        ).fetchone()[0]
        pid = conn.execute(
            "insert into participant (display_name, person_key, role, status) "
            "values ('Педагог Тест 2', %s, 'troupe', 'active') returning id",
            (make_key("Педагог Тест 2"),)).fetchone()[0]
        conn.commit()

    res = client.post("/tools/participants/attach",
                      json={"chat_id": 991003, "participant_id": pid,
                            "code": code, "discipline_code": "лeтенка"}).json()
    assert res["ok"] is False

    with _conn() as conn:
        role = conn.execute("select role from participant where id = %s", (pid,)).fetchone()[0]
    assert role == "troupe", "неизвестная дисциплина не должна менять запись"

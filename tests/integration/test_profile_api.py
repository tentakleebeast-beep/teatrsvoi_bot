"""Анкета через бота: чтение и правка своей анкеты (docs/development/43 §5, V-009)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_своя_анкета_читается(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 780100, "Анкета Чтение")
        project = conn.execute(
            "insert into project (name, kind, status) values ('Чтение Спектакль','show','active') "
            "returning id").fetchone()[0]
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, project_id)
                        values (%s,'Роль Одна','role',%s)""", (pid, project))
        conn.commit()

    body = client.get("/tools/profile/me",
                      headers={"Authorization": "Bearer test-token",
                               "X-Actor-Chat-Id": "780100"}).json()
    assert body["ok"] is True
    assert body["travel_minutes"] == 60
    assert body["roles"][0]["alias"] == "Роль Одна"
    assert body["roles"][0]["project_name"] == "Чтение Спектакль"


def test_правка_времени_в_пути(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 780101, "Анкета Правка")

    body = client.post("/tools/profile/me", json={"travel_minutes": 20},
                       headers={"Authorization": "Bearer test-token",
                                "X-Actor-Chat-Id": "780101"}).json()
    assert body["ok"] is True and body["travel_minutes"] == 20

    with psycopg.connect(_test_dsn()) as conn:
        value = conn.execute("select travel_minutes from participant "
                             "where tg_chat_id = 780101").fetchone()[0]
    assert value == 20


def test_границы_времени_проверяются(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 780102, "Анкета Границы")

    r = client.post("/tools/profile/me", json={"travel_minutes": 999},
                    headers={"Authorization": "Bearer test-token",
                             "X-Actor-Chat-Id": "780102"})
    assert r.status_code in (400, 422)


def test_чужая_анкета_недоступна(client):
    r = client.get("/tools/profile/me",
                   headers={"Authorization": "Bearer test-token",
                            "X-Actor-Chat-Id": "999999"})
    assert r.status_code in (401, 403)


def test_новое_короткое_имя_добавляется_не_стирая_старые(client):
    """Знакомство в чате спрашивает одно имя (обращение №17) — список не заменяем."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 780102, "Анкета Псевдонимы")
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind)
                        values (%s, 'Вика', 'short')""", (pid,))
        conn.commit()

    body = client.post("/tools/profile/me", json={"alias_add": "Виктория"},
                       headers={"Authorization": "Bearer test-token",
                                "X-Actor-Chat-Id": "780102"}).json()
    assert body["ok"] is True and body["alias_add"] == "Виктория"

    with psycopg.connect(_test_dsn()) as conn:
        aliases = {row[0] for row in conn.execute(
            "select alias from person_alias where participant_id = %s "
            "and alias_kind = 'short'", (pid,))}
    assert aliases == {"Вика", "Виктория"}


def test_пустое_короткое_имя_отклоняется(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 780103, "Анкета Пусто")
    r = client.post("/tools/profile/me", json={"alias_add": "я"},
                    headers={"Authorization": "Bearer test-token",
                             "X-Actor-Chat-Id": "780103"})
    assert r.status_code == 400 and r.json()["detail"] == "alias_bad_length"


# --- V-009 / обращение №17: поля знакомства сохраняются и читаются ------------

def test_поля_знакомства_сохраняются_V009(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 780110, "Знакомство Поля")

    body = client.post("/tools/profile/me", json={
        "phone": "+7 917 123-45-67", "birth_date": "1990-03-12",
        "plays": "Чайка, Гамлет", "roles_note": "Треплев",
        "interests": "импровизация"},
        headers={"Authorization": "Bearer test-token",
                 "X-Actor-Chat-Id": "780110"}).json()
    assert body["ok"] is True

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select phone, birth_date, plays, roles_note, interests "
                           "from participant where tg_chat_id = 780110").fetchone()
    assert row == ("+79171234567", row[1], "Чайка, Гамлет", "Треплев",
                   "импровизация")
    assert row[1].isoformat() == "1990-03-12"

    me = client.get("/tools/profile/me",
                    headers={"Authorization": "Bearer test-token",
                             "X-Actor-Chat-Id": "780110"}).json()
    assert me["phone"] == "+79171234567"
    assert me["birth_date"] == "1990-03-12"
    assert me["plays"] == "Чайка, Гамлет"
    assert me["roles_note"] == "Треплев"
    assert me["interests"] == "импровизация"


def test_битый_телефон_и_дата_отклоняются_V009(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 780111, "Знакомство Брак")

    for payload in ({"phone": "12345"}, {"birth_date": "1899-01-01"}):
        r = client.post("/tools/profile/me", json=payload,
                        headers={"Authorization": "Bearer test-token",
                                 "X-Actor-Chat-Id": "780111"})
        assert r.status_code in (400, 422), payload

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select phone, birth_date from participant "
                           "where tg_chat_id = 780111").fetchone()
    assert row == (None, None)

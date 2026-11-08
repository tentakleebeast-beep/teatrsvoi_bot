"""UC-06 (участники и заявки), UC-07 (код приглашения) — приёмка из docs/development/28."""

import datetime as dt

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int | None, name: str, status: str = "active",
            role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,%s) returning id",
        (chat_id, name, make_key(name), role, status)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def test_uc06_архивные_не_показываются(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364801, "Живой Участник")
        _person(conn, 250364802, "Архивный Участник", status="archived")

    body = client.get("/tools/participants", headers=HEADERS).json()
    names = [row["display_name"] for row in body["data"]]
    assert "Живой Участник" in names
    assert "Архивный Участник" not in names


def test_uc06_заявок_нет_пустой_список(client):
    body = client.get("/admin/pending", headers=HEADERS).json()
    assert body["ok"] is True
    assert body["data"] == []


def test_uc06_подтверждение_меняет_статус_и_пишет_аудит(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 250364803, "Заявка Тест", status="pending")

    approved = client.post("/admin/approve", headers=HEADERS,
                           json={"participant_id": pid, "approve": True}).json()
    assert approved["ok"] is True

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from participant where id = %s",
                              (pid,)).fetchone()[0]
        audits = [row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(pid),)).fetchall()]
    assert status == "active"
    assert any("approve" in (action or "") for action in audits), audits


def test_uc07_код_создаётся_с_ролью_и_сроком(client):
    body = client.post("/admin/invite", headers=HEADERS,
                       json={"role": "org", "ttl_days": 7}).json()
    assert body["ok"] is True
    assert len(body["code"]) == 8
    assert body["role"] == "org"

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select role, expires_at, uses, max_uses "
                           "from invite_code where code = %s", (body["code"],)).fetchone()
    assert row[0] == "org" and row[2] == 0 and row[3] == 1


def test_uc07_код_админа_запрещён(client):
    response = client.post("/admin/invite", headers=HEADERS, json={"role": "admin"})
    assert response.status_code == 400


def test_uc07_неизвестная_роль_отклоняется(client):
    response = client.post("/admin/invite", headers=HEADERS, json={"role": "student"})
    assert response.status_code in (400, 422)


def test_uc07_просроченный_код_не_работает(client):
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""insert into invite_code (code, role, created_by, expires_at, max_uses)
                        values ('EXPIRED1','troupe',0,%s,1)""",
                     (dt.datetime.now(dt.UTC) - dt.timedelta(days=1),))
        conn.commit()

    body = client.post("/tools/participants/check_code", headers=HEADERS,
                       json={"code": "EXPIRED1", "chat_id": 250364810}).json()
    assert body["ok"] is False
    assert body["error"] in ("code_not_found", "code_expired", "expired")


def test_uc07_использованный_код_не_работает(client):
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""insert into invite_code (code, role, created_by, expires_at,
                                                 max_uses, uses)
                        values ('USEDCODE','troupe',0,%s,1,1)""",
                     (dt.datetime.now(dt.UTC) + dt.timedelta(days=3),))
        conn.commit()

    body = client.post("/tools/participants/check_code", headers=HEADERS,
                       json={"code": "USEDCODE", "chat_id": 250364811}).json()
    assert body["ok"] is False

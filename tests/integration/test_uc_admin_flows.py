"""UC-06 (люди) и UC-08 (проверка кода) — приёмка из docs/development/28.

UC-06 «заявки» и UC-07 «выпуск кода из чата» после docs/development/182 §4.1
и §4.3.3 больше не существуют: заявок нет, коды выпускаются только из Mini App.
Их приёмка живёт в `tests/integration/test_personal_invite_code.py`
и `test_admin_invites.py`.

Обоснование: FR-A2 (код приглашения), FR-G16 (первый вход), docs/development/28
UC-06, UC-08; docs/development/182 §4.1, §4.3.3.
"""

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


def _code(code: str, expires_in: int, *, participant_id: int | None = None,
          uses: int = 0) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""insert into invite_code (code, role, participant_id, created_by,
                                                 expires_at, max_uses, uses)
                        values (%s,'troupe',%s,0,%s,1,%s)""",
                     (code, participant_id,
                      dt.datetime.now(dt.UTC) + dt.timedelta(days=expires_in), uses))
        conn.commit()


def test_uc06_архивные_не_показываются(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364801, "Живой Участник")
        _person(conn, 250364802, "Архивный Участник", status="archived")

    body = client.get("/tools/participants", headers=HEADERS).json()
    names = [row["display_name"] for row in body["data"]]
    assert "Живой Участник" in names
    assert "Архивный Участник" not in names


def test_uc06_человек_без_telegram_виден_в_списке(client):
    """Новый человек заводится без Telegram (V-117): список его показывает."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, None, "Заведённый Вручную")

    body = client.get("/tools/participants", headers=HEADERS).json()
    names = [row["display_name"] for row in body["data"]]
    assert "Заведённый Вручную" in names


def test_uc08_просроченный_код_не_работает(client):
    _code("EXPIRED1", -1)
    body = client.post("/tools/participants/check_code", headers=HEADERS,
                       json={"code": "EXPIRED1", "chat_id": 250364810}).json()
    assert body["ok"] is False
    assert body["error"] == "code_expired"


def test_uc08_использованный_код_не_работает(client):
    _code("USEDCODE", 3, uses=1)
    body = client.post("/tools/participants/check_code", headers=HEADERS,
                       json={"code": "USEDCODE", "chat_id": 250364811}).json()
    assert body["ok"] is False
    assert body["error"] == "code_exhausted"

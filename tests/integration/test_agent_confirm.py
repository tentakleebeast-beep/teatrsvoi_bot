"""Подтверждение предложения агента: токен, одна запись, журнал (A-3, docs/development/108 §8).

Проверяем главное обещание карты: агент предлагает, а пишет проверенный код после
нажатия человеком.

  * до подтверждения в `budget_tx` нет новой операции;
  * подтверждение создаёт ровно одну операцию — тем же путём, что форма;
  * повторное нажатие и истёкший токен — отказ без дубля;
  * «Отмена» не пишет ничего и оставляет след в `audit_event`;
  * без права `budget.write` предложения с токеном нет.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

ADMIN_CHAT = 123456789
STRANGER_CHAT = 570124


@pytest.fixture()
def conn(test_db: str):
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        yield connection


def _category(conn, direction: str = "expense") -> str:
    return conn.execute(
        "select name from category where direction = %s order by name limit 1",
        (direction,)).fetchone()["name"]


def _propose(client, auth, **overrides) -> dict:
    body = {"chat_id": ADMIN_CHAT, "tool": "budget.tx",
            "args": {"tx_type": "expense", "amount": 3000,
                     "category": overrides.pop("category", None),
                     "purpose": "костюмы"},
            "human_text": "Записать расход: 3000 ₽"}
    body["args"].update(overrides)
    response = client.post("/tools/agent/propose", json=body, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client, auth, token: str, chat_id: int = ADMIN_CHAT) -> dict:
    response = client.post("/tools/agent/confirm",
                           json={"token": token, "chat_id": chat_id}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def _cancel(client, auth, token: str, chat_id: int = ADMIN_CHAT) -> dict:
    response = client.post("/tools/agent/cancel",
                           json={"token": token, "chat_id": chat_id}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def _tx_count(conn) -> int:
    return conn.execute("select count(*) as n from budget_tx").fetchone()["n"]


def _audit_actions(conn) -> list[str]:
    return [row["action"] for row in conn.execute(
        "select action from audit_event order by id").fetchall()]


def _stranger(conn, chat_id: int = STRANGER_CHAT) -> dict:
    """Действующий участник без права записи: труппа, никаких точечных выдач."""
    row = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s, 'Без права', 'без-права', 'troupe', 'active')"
        " returning id", (chat_id,)).fetchone()
    conn.execute("insert into participant_role (participant_id, role) values (%s, 'troupe')"
                 " on conflict do nothing", (row["id"],))
    conn.commit()   # человек должен быть виден сервису, иначе это «неизвестный участник»
    return {"id": row["id"]}


def test_предложение_даёт_токен_и_не_пишет_операцию(client, auth, conn) -> None:
    category = _category(conn)
    result = _propose(client, auth, category=category)

    assert result["ok"] is True and result["token"]
    assert result["proposal"]["args"]["amount"] == 3000
    assert result["proposal"]["args"]["category"] == category
    assert result["ttl_seconds"] == 300
    # до нажатия кнопки операции нет — только строка предложения
    assert _tx_count(conn) == 0
    row = conn.execute("select status, chat_id, expires_at > now() as fresh"
                       " from agent_proposal where token = %s",
                       (result["token"],)).fetchone()
    assert row["status"] == "pending" and row["chat_id"] == ADMIN_CHAT and row["fresh"] is True


def test_подтверждение_создаёт_ровно_одну_операцию(client, auth, conn) -> None:
    category = _category(conn)
    token = _propose(client, auth, category=category)["token"]

    result = _confirm(client, auth, token)

    assert result["ok"] is True
    assert result["tx"]["amount"] == 3000
    assert _tx_count(conn) == 1
    tx = conn.execute("select tx_type, amount, idempotency_key, status from budget_tx"
                      ).fetchone()
    assert tx["tx_type"] == "expense" and float(tx["amount"]) == 3000
    assert tx["idempotency_key"] == "agent:confirm:" + token
    assert tx["status"] == "confirmed"
    actions = _audit_actions(conn)
    assert "agent.propose" in actions and "agent.confirm" in actions
    confirm_row = conn.execute(
        "select after, actor_type from audit_event where action = 'agent.confirm'").fetchone()
    assert confirm_row["after"]["proposal_id"]
    # журнал различает роли: предложил агент, подтвердил человек
    assert confirm_row["actor_type"] == "human"


def test_повторное_нажатие_не_создаёт_вторую_операцию(client, auth, conn) -> None:
    token = _propose(client, auth, category=_category(conn))["token"]
    assert _confirm(client, auth, token)["ok"] is True

    again = _confirm(client, auth, token)

    assert again["ok"] is False and again["error"] == "token_used"
    assert _tx_count(conn) == 1
    # отказ тоже попал в журнал: видно, что кнопку нажали второй раз
    assert "agent.confirm.refused" in _audit_actions(conn)


def test_истёкший_токен_отказ_без_дубля(client, auth, conn) -> None:
    token = _propose(client, auth, category=_category(conn))["token"]
    conn.execute("update agent_proposal set expires_at = now() - interval '1 second'"
                 " where token = %s", (token,))
    conn.commit()   # иначе строка остаётся заблокированной и подтверждение ждёт её

    result = _confirm(client, auth, token)

    assert result["ok"] is False and result["error"] == "token_expired"
    assert _tx_count(conn) == 0
    status = conn.execute("select status from agent_proposal where token = %s",
                          (token,)).fetchone()["status"]
    assert status == "expired"


def test_отмена_ничего_не_пишет_и_фиксируется_в_журнале(client, auth, conn) -> None:
    token = _propose(client, auth, category=_category(conn))["token"]

    result = _cancel(client, auth, token)

    assert result["ok"] is True and result["note"] == "отменено"
    assert _tx_count(conn) == 0
    assert "agent.cancel" in _audit_actions(conn)
    # после отмены подтверждение уже не сработает
    after = _confirm(client, auth, token)
    assert after["ok"] is False and after["error"] == "token_cancelled"
    assert _tx_count(conn) == 0


def test_человек_без_права_записи_не_получает_предложение(client, conn) -> None:
    _stranger(conn)
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(STRANGER_CHAT)}

    result = _propose(client, headers, category=_category(conn))

    assert result["ok"] is False and result["error"] == "forbidden"
    assert "token" not in result
    assert conn.execute("select count(*) as n from agent_proposal").fetchone()["n"] == 0
    assert _tx_count(conn) == 0


def test_чужой_чат_не_может_подтвердить_предложение(client, auth, conn) -> None:
    token = _propose(client, auth, category=_category(conn))["token"]

    result = _confirm(client, auth, token, chat_id=ADMIN_CHAT + 1)

    assert result["ok"] is False
    assert result["error"] in ("token_chat_mismatch", "token_foreign")
    assert _tx_count(conn) == 0


def test_аргументы_проверяет_инструмент_а_не_клиент(client, auth, conn) -> None:
    """Кривая категория и нулевая сумма — отказ до токена, карточку рисовать нечего."""
    bad_category = _propose(client, auth, category="Такой категории нет")
    zero = _propose(client, auth, category=_category(conn), amount=0)

    assert bad_category["ok"] is False and bad_category["error"] == "bad_category"
    assert zero["ok"] is False and zero["error"] == "bad_amount"
    assert conn.execute("select count(*) as n from agent_proposal").fetchone()["n"] == 0
    assert _tx_count(conn) == 0


def test_маршруты_подтверждения_есть_в_openapi(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/tools/agent/propose" in paths
    assert "/tools/agent/confirm" in paths
    assert "/tools/agent/cancel" in paths

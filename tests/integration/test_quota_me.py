"""«Сколько мне осталось»: читающий маршрут `GET /tools/quota/me` (05.11.2026).

Кнопка отвечает цифрами, но не тратит то, о чём спрашивают:
  * остаток берётся из `v_llm_balance` (месячные токены, дневные запросы, стоимость);
  * маршрут ничего не пишет: строка в `llm_usage` не появляется;
  * незаданный лимит называется словами «лимит не задан», а не нулём.

Обоснование: решение владельца 05.11.2026, docs/development/82 §3.3, FR-E6 (лимиты AI).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn


def _add_person(chat_id: int, name: str) -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            "insert into participant (tg_chat_id, display_name, role, status) "
            "values (%s, %s, 'troupe', 'active') returning id", (chat_id, name)).fetchone()
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'troupe') "
                     "on conflict do nothing", (row[0],))
    return row[0]


def _person_auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _usage_rows(participant_id: int) -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute("select count(*) from llm_usage where participant_id = %s",
                            (participant_id,)).fetchone()[0]


def _spend(participant_id: int, tokens_in: int, tokens_out: int, cost: float | None) -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            "insert into llm_usage (participant_id, model, tokens_in, tokens_out, cost_rub,"
            "                       intent, provider) values (%s,'test-model',%s,%s,%s,'agent',"
            "                       'openrouter')",
            (participant_id, tokens_in, tokens_out, cost))


def _limits(monthly_tokens: int | None, daily_requests: int | None) -> None:
    """Политика global и роль `troupe`: лимит виден, только если не задан ни один уровень."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update llm_policy set monthly_tokens = %s, daily_requests = %s "
                     "where scope in ('global', 'role') and (scope = 'global' or scope_key = 'troupe')",
                     (monthly_tokens, daily_requests))


def _saved_limits() -> list[tuple]:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute(
            "select id, monthly_tokens, daily_requests from llm_policy"
            " where scope = 'global' or (scope = 'role' and scope_key = 'troupe')").fetchall()


def _restore_limits(saved: list[tuple]) -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        for row_id, monthly, daily in saved:
            conn.execute("update llm_policy set monthly_tokens = %s, daily_requests = %s "
                         "where id = %s", (monthly, daily, row_id))


def test_квота_отдаёт_цифры_из_баланса(client, auth):
    """Остаток = лимит − расход месяца; поля приходят те же, что у `v_llm_balance`."""
    pid = _add_person(882001, "Квота Считаемая")
    _spend(pid, 1000, 500, 1.5)

    res = client.get("/tools/quota/me", headers=_person_auth(882001))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["month_tokens"] == 1500
    assert body["tokens_limit"] is not None
    assert body["tokens_left"] == body["tokens_limit"] - 1500
    assert body["month_cost_rub"] == 1.5
    # дневной лимит — политика роли (participant-строки у человека нет, global ниже роли)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        role_daily = conn.execute(
            "select daily_requests from llm_policy where scope = 'role' and scope_key = 'troupe'"
        ).fetchone()[0]
    assert body["daily_limit"] == role_daily
    # один запрос уже был: он и внесённая в баланс трата — одна и та же строка `llm_usage`
    assert body["requests_left"] == role_daily - 1
    assert body["text"], "кнопка показывает текст, а не JSON"


def test_квота_не_тратит_лимит(client, auth):
    """Ответ не вызывает модель: новой строки в `llm_usage` после вопроса нет."""
    pid = _add_person(882002, "Квота Нерасходуемая")
    before = _usage_rows(pid)

    res = client.get("/tools/quota/me", headers=_person_auth(882002))
    assert res.status_code == 200
    assert res.json()["ok"] is True

    assert _usage_rows(pid) == before, "вопрос о квоте создал расход"


def test_квота_при_пустых_лимитах_говорит_словами(client, auth):
    """`tokens_limit is null` — «лимит не задан», а не «0 из 0»."""
    pid = _add_person(882003, "Квота Безлимитная")
    _spend(pid, 700, 300, 0.5)
    saved = _saved_limits()
    _limits(None, None)
    try:
        body = client.get("/tools/quota/me", headers=_person_auth(882003)).json()
        assert body["ok"] is True
        assert body["tokens_limit"] is None
        assert body["daily_limit"] is None
        assert "лимит не задан" in body["text"]
        # расход при этом честно называется: он есть, и его видно
        assert "1 000" in body["text"]
        assert "из 0" not in body["text"]
    finally:
        _restore_limits(saved)


def test_квота_без_участника_отвечает_отказом(client, auth):
    """Незарегистрированный chat_id — понятный отказ, а не чужие цифры."""
    res = client.get("/tools/quota/me",
                     headers={"Authorization": f"Bearer {TOKEN}",
                              "X-Actor-Chat-Id": "882099"})
    assert res.status_code in (401, 403)

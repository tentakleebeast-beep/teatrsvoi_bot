"""Повторное нажатие перезаписывает ответ, а не добавляет второй (AC-5).

Что закрывает: `tests/unit/test_poll_run.py` проверяет правило выбора в чистом виде
(`merge_choice`), но не запись в `poll_answer` — то есть ровно ту половину критерия,
где ответ «не дублируется». Здесь живая схема: два нажатия по одному вопросу дают
одну строку, а значение в ней — последнее.

Обоснование: FR-G18, AC-5, `docs/development/162-bot-polls.md` §14.4.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import (
    audience,
    create_poll,
    notifications,
    person,
    token,
    tools,
)


def test_второй_выбор_обновляет_строку_ответа(client) -> None:
    """Нажали «a1», потом «a3» — в базе одна строка и в ней «a3»."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784061, "Ответ Админ", "admin")
        person(conn, 784062, "Ответ Актёр", "troupe")
    owner = token(client, 784061)
    poll_id = create_poll(client, owner)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    invited = notifications()[0]
    codes = [button["callback_data"]
             for line in invited["payload"]["markup"]["inline_keyboard"] for button in line]
    assert codes and all(len(code.encode()) <= 18 for code in codes), codes
    run_id = invited["payload"]["run_id"]
    actor = tools(784062)

    first = client.post("/tools/polls/answer", headers=actor,
                        json={"run_id": run_id, "position": 1, "action": "o", "code": "a1"})
    assert first.status_code == 200, first.text
    again = client.post("/tools/polls/answer", headers=actor,
                        json={"run_id": run_id, "position": 1, "action": "o", "code": "a2"})
    assert again.status_code == 200, again.text

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "select a.option_codes, a.text_value from poll_answer a "
            "join poll_question q on q.id = a.question_id "
            "where a.run_id = %s and q.position = 1", (run_id,)).fetchall()
    assert len(rows) == 1, f"ответов на вопрос {len(rows)} вместо одного"
    assert rows[0]["option_codes"] == ["a2"], rows[0]
    assert rows[0]["text_value"] is None

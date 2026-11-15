"""Черновик прохождения переживает перезапуск gateway (AC-8).

Состояние бота (`MemoryStorage`) при деплое пустеет; в нём лежит только пара
`(run, position)` на время свободного ввода. Сами ответы и указатель — в
`poll_run`/`poll_answer`, поэтому «Продолжить» после рестарта открывает вопрос 3,
а ответы 1–2 на месте.

Обоснование: FR-G18, AC-8, `docs/development/162-bot-polls.md` §14.4 (D4).
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


def _started(client) -> tuple[dict, int, int]:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784031, "Опрос Админ", "admin")
        person(conn, 784032, "Опрос Актёр", "troupe")
    headers = token(client, 784031)
    poll_id = create_poll(client, headers)
    audience(client, headers, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    client.post("/webapp/polls/start", headers=headers, json={"poll_id": poll_id})
    run_id = notifications()[0]["payload"]["run_id"]
    return headers, poll_id, run_id


def test_прогресс_читается_из_базы_а_не_из_состояния(client):
    _headers, _poll_id, run_id = _started(client)
    actor = tools(784032)

    first = client.post("/tools/polls/answer", headers=actor,
                        json={"run_id": run_id, "position": 1, "action": "o", "code": "a2"})
    assert first.json()["step"]["position"] == 2
    second = client.post("/tools/polls/answer", headers=actor,
                         json={"run_id": run_id, "position": 2, "action": "text", "text": "4"})
    assert second.json()["step"]["position"] == 3

    # «Перезапуск»: никакого состояния нет — только БД и адрес прохождения.
    mine = client.get("/tools/polls/mine", headers=actor)
    assert mine.status_code == 200, mine.text
    runs = mine.json()["runs"]
    assert runs[0]["run_id"] == run_id and runs[0]["position"] == 3

    step = client.get(f"/tools/polls/step?run_id={run_id}", headers=actor)
    assert step.status_code == 200, step.text
    assert step.json()["step"]["position"] == 3
    assert step.json()["step"]["text"] == "Что улучшить?"

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        answers = conn.execute(
            "select q.position, a.option_codes, a.number_value from poll_answer a "
            "join poll_question q on q.id = a.question_id "
            "where a.run_id = %s order by q.position", (run_id,)).fetchall()
    assert [row["position"] for row in answers] == [1, 2]
    assert answers[0]["option_codes"] == ["a2"]
    assert float(answers[1]["number_value"]) == 4


def test_назад_возвращает_на_предыдущий_вопрос_с_ответом(client):
    _headers, _poll_id, run_id = _started(client)
    actor = tools(784032)
    client.post("/tools/polls/answer", headers=actor,
                json={"run_id": run_id, "position": 1, "action": "o", "code": "a1"})
    back = client.post("/tools/polls/answer", headers=actor,
                       json={"run_id": run_id, "position": 2, "action": "b"})
    step = back.json()["step"]
    assert step["position"] == 1
    assert step["chosen"] == ["a1"]


def test_пропустить_работает_только_у_необязательного(client):
    _headers, _poll_id, run_id = _started(client)
    actor = tools(784032)
    required = client.post("/tools/polls/answer", headers=actor,
                           json={"run_id": run_id, "position": 1, "action": "s"})
    assert required.status_code == 409, required.text

    optional = client.post("/tools/polls/answer", headers=actor,
                           json={"run_id": run_id, "position": 1, "action": "o", "code": "a1"})
    assert optional.json()["step"]["position"] == 2
    client.post("/tools/polls/answer", headers=actor,
                json={"run_id": run_id, "position": 2, "action": "text", "text": "3"})
    skipped = client.post("/tools/polls/answer", headers=actor,
                          json={"run_id": run_id, "position": 3, "action": "s"})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["done"] is True
    assert skipped.json()["step"]["position"] == 3

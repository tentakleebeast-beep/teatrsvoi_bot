"""Приглашение через очередь с `payload.markup`; шаг диалога идёт сразу (AC-4, AC-13, AC-15).

Проверяется на живой схеме: `start` создаёт `poll_run` и записи `notification`
с кнопками, отправитель берёт `markup` из `payload` (Telegram не дёргается —
sender подменён), тихие часы переносят приглашение на утро, а ответ на нажатие
уходит без ожидания лимита. Живых рассылок нет: два фейковых chat_id.

Обоснование: FR-G18, AC-4, AC-13, AC-15, `docs/development/162-bot-polls.md` §14.6.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import psycopg
import pytest
import send_notifications as sender
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

EVENING = datetime(2026, 11, 15, 21, 30, tzinfo=UTC)
MORNING = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _schedule_at(moment: datetime) -> None:
    """Поставить очередь на «чуть раньше момента»: иначе now() в базе позже прогона."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update notification set scheduled_at = %s "
                     "where kind in ('poll', 'poll_reminder')", (moment,))


def _setup(client):
    """Админ, два получателя-актёра с Telegram и один без — набор «роль troupe».

    Набор по роли, а не «все активные»: чистильщик тестов каждый раз создаёт ещё
    одного организатора, и «все активные» зависел бы от соседнего кода.
    """
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784021, "Опрос Админ", "admin")
        person(conn, 784022, "Опрос Первый", "troupe")
        person(conn, 784023, "Опрос Второй", "troupe")
        quiet = person(conn, 784024, "Опрос Без Телеграма", "troupe")
        conn.execute("update participant set tg_chat_id = null where id = %s", (quiet,))
        conn.commit()
    headers = token(client, 784021)
    poll_id = create_poll(client, headers)
    audience(client, headers, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    return headers, poll_id


def test_приглашение_несёт_кнопки_из_payload_markup(client):
    headers, poll_id = _setup(client)
    started = client.post("/webapp/polls/start", headers=headers, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    assert started.json()["invited"] == 2
    assert started.json()["skipped_no_chat"] == 1

    rows = notifications()
    assert len(rows) == 2
    for row in rows:
        assert row["kind"] == "poll"
        markup = row["payload"]["markup"]
        assert markup["inline_keyboard"], "у приглашения нет кнопок"
        data = markup["inline_keyboard"][0][0]["callback_data"]
        assert data.startswith("p:o:") and len(data.encode()) <= 18
        assert row["payload"]["title"].endswith("вопрос 1 из 3")

    sent: list[tuple] = []
    _schedule_at(MORNING)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        stats = sender.deliver(
            conn, MORNING, apply_changes=True,
            sender=lambda chat_id, text, markup=None: (sent.append((chat_id, text, markup))
                                                       or (True, "")),
            out=lambda *_: None)
    assert stats["sent"] == 2
    assert all(markup for _, _, markup in sent)
    assert all("вопрос 1 из 3" in text for _, text, _ in sent)


def test_тихие_часы_переносят_приглашение_а_ответ_идёт_сразу(client):
    """AC-13: push ждёт утра, шаг диалога — нет: иначе 11 вопросов не пройти."""
    headers, poll_id = _setup(client)
    client.post("/webapp/polls/start", headers=headers, json={"poll_id": poll_id})

    _schedule_at(EVENING)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        stats = sender.deliver(conn, EVENING, apply_changes=True,
                               sender=lambda *_, **__: pytest.fail("push ушёл в тихие часы"),
                               out=lambda *_: None)
        scheduled = conn.execute(
            "select scheduled_at from notification where kind = 'poll' order by id"
        ).fetchall()
    assert stats["sent"] == 0 and stats["postponed"] == 2
    assert all(row["scheduled_at"].hour == 8 for row in scheduled)

    run = next(row["payload"]["run_id"] for row in notifications()
               if row["tg_chat_id"] == 784022)
    answer = client.post("/tools/polls/answer", headers=tools(784022),
                         json={"run_id": run, "position": 1, "action": "o", "code": "a1"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["ok"] is True and answer.json()["done"] is False
    assert answer.json()["step"]["position"] == 2
    # Шаг не породил новую запись очереди: он ответ на действие.
    assert len(notifications()) == 2


def test_закрытый_опрос_больше_не_принимает_ответы(client):
    headers, poll_id = _setup(client)
    client.post("/webapp/polls/start", headers=headers, json={"poll_id": poll_id})
    run = next(row["payload"]["run_id"] for row in notifications()
               if row["tg_chat_id"] == 784022)
    closed = client.post("/webapp/polls/close", headers=headers, json={"poll_id": poll_id})
    assert closed.status_code == 200, closed.text

    answer = client.post("/tools/polls/answer", headers=tools(784022),
                         json={"run_id": run, "position": 1, "action": "o", "code": "a1"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["closed"] is True

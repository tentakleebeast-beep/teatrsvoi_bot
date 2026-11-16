"""Живой сценарий V-096: опрос создан в тихие часы, пройден ночью, в 08:00 тишина.

Отличие от `test_polls_invite_cancel.py`: там строка очереди поставлена на утро
руками. Здесь приглашение сначала проходит настоящие тихие часы (`deliver()` в
ночь переносит его на утро), человек проходит опрос в Mini App, и только потом
наступает утро — проверяется, что перенесённое приглашение не отправилось.

Обоснование: FR-G18, AC-5, AC-8, V-096, `docs/development/164-miniapp-home-polls.md`
§12.7, §12.9.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import send_notifications as sender
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import (
    audience,
    create_poll,
    notifications,
    person,
    token,
)

#: 01:30 по Самаре (UTC+4) — тихие часы 20:00–08:00: deliver переносит приглашение на утро.
NIGHT = datetime(2026, 11, 16, 21, 30, tzinfo=UTC)


def _started(client) -> tuple[int, int]:
    """Опрос отправлен труппе, очередь «на чуть раньше ночи»."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 788001, "Ночь Админ", "admin")
        person(conn, 788002, "Ночь Актёр", "troupe")
    owner = token(client, 788001)
    poll_id = create_poll(client, owner)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    started = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    run_id = next(row["payload"]["run_id"] for row in notifications()
                  if row["tg_chat_id"] == 788002)
    # now() в базе позже момента прогона — иначе очередь ещё не «готова».
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update notification set scheduled_at = %s where kind = 'poll'",
                     (NIGHT - timedelta(minutes=5),))
    return poll_id, run_id


def _invite(run_id: int) -> dict:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute(
            "select * from notification where kind = 'poll' and payload->>'run_id' = %s",
            (str(run_id),)).fetchone()


def _deliver(moment: datetime) -> tuple[dict, list[tuple]]:
    """Прогон отправителя без Telegram: возвращает счётчики и список отправок."""
    sent: list[tuple] = []
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        stats = sender.deliver(conn, moment,
                               sender=lambda *args: sent.append(args) or (True, ""),
                               out=lambda *args: None)
    return stats, sent


def _pass_whole(client, run_id: int) -> None:
    """Пройти все три вопроса анкеты: single, number, необязательный text."""
    actor = token(client, 788002)
    for body in ({"action": "o", "code": "a1"},
                 {"action": "text", "text": "4"},
                 {"action": "s"}):
        answer = client.post("/webapp/polls/answer", headers=actor,
                             json={"run_id": run_id, **body})
        assert answer.status_code == 200, answer.text
    assert answer.json()["done"] is True, answer.text


def test_ночью_приглашение_перенесено_а_после_прохождения_не_уходит(client) -> None:
    """AC-5: в 01:30 приглашение ждёт утра, прохождение ночью отменяет его, в 08:00 пусто."""
    _poll_id, run_id = _started(client)

    # Ночь: приглашение не отправляется, а переносится на утро — то самое «в 08:00 придёт».
    stats, sent = _deliver(NIGHT)
    assert stats["sent"] == 0 and sent == [], stats
    assert stats["postponed"] == 1, "тихие часы не перенесли приглашение на утро"
    row = _invite(run_id)
    assert row["status"] == "scheduled" and row["sent_at"] is None, dict(row)
    wake = row["scheduled_at"]
    assert wake > NIGHT, f"перенос не на утро: {wake}"

    # Ночь: человек проходит опрос в Mini App целиком.
    _pass_whole(client, run_id)
    row = _invite(run_id)
    assert row["status"] == "cancelled", dict(row)
    assert row["sent_at"] is None
    assert row["last_error"] == "пройдено в приложении"

    # Утро: перенесённое приглашение не берётся в отправку и не появляется второй строкой.
    stats, sent = _deliver(wake + timedelta(minutes=1))
    assert stats["ready"] == 0, stats
    assert stats["sent"] == 0 and sent == [], "push ушёл после пройденного ночью опроса"
    invites = [item for item in notifications()
               if item["kind"] == "poll" and item["payload"].get("run_id") == run_id]
    assert len(invites) == 1, f"строк приглашения {len(invites)}, ожидалась одна"
    assert invites[0]["status"] == "cancelled"
    assert invites[0]["sent_at"] is None

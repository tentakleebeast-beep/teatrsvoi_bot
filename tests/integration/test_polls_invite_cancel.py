"""Ответ в приложении отменяет отложенное приглашение на опрос (V-096, E8-D4).

Приглашение — строка `notification` с `kind='poll'` и `payload->>'run_id'`. Пока
она `scheduled` (push в 08:00) или `failed`, ответ человека в Mini App делает её
ненужной: человек уже отвечает, и push пришёл бы вдогонку. `sent` и `skipped` не
трогаются — отправленное не отзывается.

Критерии `docs/development/164-miniapp-home-polls.md` §12.9: AC-5 (начал в
приложении — строка `cancelled`, `deliver()` ничего не отправляет), AC-6 (`sent`
не изменился), AC-7 (`failed` тоже отменяется, `retry_failed` её не возвращает),
AC-8 (повторный `polls_start` — `409`, новых `kind='poll'` нет).

Обоснование: FR-G18, AC-5, AC-6, AC-7, AC-8, V-096,
`docs/development/164-miniapp-home-polls.md` §12.7, §12.9.
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

#: Полдень по UTC — 16:00 в Самаре: тихие часы (20:00–08:00) не мешают проверке.
NOON = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _started(client) -> tuple[int, int]:
    """Опрос отправлен труппе: номер опроса и прохождение актёра."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 786001, "Отмена Админ", "admin")
        person(conn, 786002, "Отмена Актёр", "troupe")
    owner = token(client, 786001)
    poll_id = create_poll(client, owner)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    started = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    run_id = next(row["payload"]["run_id"] for row in notifications()
                  if row["tg_chat_id"] == 786002)
    # Очередь «на чуть раньше полудня»: иначе `now()` в базе окажется позже прогона.
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update notification set scheduled_at = %s where kind = 'poll'",
                     (NOON - timedelta(hours=1),))
    return poll_id, run_id


def _invite(run_id: int) -> dict:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute(
            "select * from notification where kind = 'poll' and payload->>'run_id' = %s",
            (str(run_id),)).fetchone()


def _answer(client, run_id: int) -> None:
    """Первый шаг в приложении — этого достаточно: прохождение перешло в `in_progress`."""
    answer = client.post("/webapp/polls/answer", headers=token(client, 786002),
                         json={"run_id": run_id, "action": "o", "code": "a1"})
    assert answer.status_code == 200, answer.text


def test_начало_в_приложении_отменяет_отложенное_приглашение(client) -> None:
    """AC-5: строка `cancelled`, `sent_at` пуст, `deliver()` ничего не берёт."""
    _poll_id, run_id = _started(client)
    _answer(client, run_id)

    row = _invite(run_id)
    assert row["status"] == "cancelled"
    assert row["sent_at"] is None
    assert row["last_error"] == "пройдено в приложении"

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        sent: list[tuple] = []
        result = sender.deliver(conn, NOON,
                                sender=lambda *args: sent.append(args) or (True, ""),
                                out=lambda *args: None)
    assert result["ready"] == 0, "отменённое приглашение попало в очередь отправки"
    assert sent == [], "push ушёл после того, как человек ответил в приложении"
    assert _invite(run_id)["status"] == "cancelled"


def test_отправленное_приглашение_не_трогается(client) -> None:
    """AC-6: `sent` — это уже доставленное сообщение, отзывать его нечем."""
    _poll_id, run_id = _started(client)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update notification set status = 'sent', sent_at = %s "
                     "where kind = 'poll'", (NOON,))
    _answer(client, run_id)

    row = _invite(run_id)
    assert row["status"] == "sent"
    assert row["sent_at"] is not None
    assert row["last_error"] is None


def test_проваленное_приглашение_отменяется_и_не_возвращается(client) -> None:
    """AC-7: `failed` от ошибки настройки тоже отменяется — `retry_failed` её не берёт."""
    _poll_id, run_id = _started(client)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update notification set status = 'failed', attempts = 3, "
                     "last_error = 'HTTP 401 Unauthorized' where kind = 'poll'")
    _answer(client, run_id)

    assert _invite(run_id)["status"] == "cancelled"
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        result = sender.retry_failed(conn, NOON, out=lambda *args: None)
    assert result["retried"] == 0, "отменённое приглашение вернулось в очередь"
    assert _invite(run_id)["status"] == "cancelled"


def test_повторная_отправка_не_создаёт_новых_приглашений(client) -> None:
    """AC-8: второй `polls_start` — `409 already_running`, новых `kind='poll'` ноль."""
    poll_id, run_id = _started(client)
    owner = token(client, 786001)
    _answer(client, run_id)

    again = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert again.status_code == 409, again.text
    assert again.json().get("code") == "already_running"

    invites = [row for row in notifications() if row["kind"] == "poll"]
    assert len(invites) == 1, f"приглашений {len(invites)}, ожидалось одно"
    assert invites[0]["status"] == "cancelled"

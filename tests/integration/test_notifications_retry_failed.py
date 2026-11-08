"""Разбор записей failed после устранения причины ошибки (Ш-35, INT-002).

Дефект: семь уведомлений 27.10 исчерпали три попытки на ошибке
`TELEGRAM_TOKEN не задан` и после починки отправки остались мёртвыми: `attempts`
исчерпан, статус failed, в выборку отправки такие записи не попадают.
"""

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from scripts import send_notifications as sender
from tests.conftest import _test_dsn

TZ = timezone(timedelta(hours=4))
MOMENT = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
CHAT = 556001
TOKEN_ERROR = "TELEGRAM_TOKEN не задан"
API_ERROR = "HTTP 400: Bad Request: message is too long"


def _connect():
    return psycopg.connect(_test_dsn(), row_factory=dict_row)


def _person(conn, chat_id: int) -> int:
    return conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, %s, %s) returning id",
        (chat_id, "Получатель", f"retry-{chat_id}", "org", "active")
    ).fetchone()["id"]


def _failed(conn, recipient_id: int, *, hours_ago: float,
            error: str = TOKEN_ERROR) -> int:
    """Запись, исчерпавшая попытки: ровно то, что осталось от 27.10."""
    return conn.execute(
        "insert into notification (recipient_id, kind, payload, dedupe_key, "
        "scheduled_at, status, attempts, last_error) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (recipient_id, "feedback",
         psycopg.types.json.Jsonb({"title": "Обращение"}),
         f"failed:{recipient_id}:{hours_ago}",
         MOMENT - timedelta(hours=hours_ago), "failed", 3, error)
    ).fetchone()["id"]


def test_ошибка_настройки_опознаётся_а_ошибка_api_нет():
    assert sender.is_config_error(TOKEN_ERROR)
    assert sender.is_config_error("HTTP 401: Unauthorized")
    assert sender.is_config_error("HTTP 404: Not Found")
    assert not sender.is_config_error(API_ERROR)
    assert not sender.is_config_error(None)


def test_свежая_ошибка_настройки_возвращается_в_очередь(client):
    with _connect() as conn:
        person = _person(conn, CHAT)
        note = _failed(conn, person, hours_ago=3)
        conn.commit()

        stats = sender.retry_failed(conn, MOMENT, apply_changes=True,
                                    out=lambda *_: None)

        assert stats == {"failed": 1, "retried": 1, "skipped": 0}
        row = conn.execute(
            "select status, attempts, scheduled_at, last_error from notification "
            "where id = %s", (note,)).fetchone()
        assert row["status"] == "scheduled"
        assert row["attempts"] == 0
        assert row["scheduled_at"] == MOMENT
        assert row["last_error"] == TOKEN_ERROR

        result = sender.deliver(conn, MOMENT, apply_changes=True,
                                sender=lambda chat_id, text: (True, ""),
                                out=lambda *_: None)
        assert result["sent"] == 1


def test_старая_ошибка_настройки_помечается_skipped(client):
    """Ждала больше суток: сообщение потеряло смысл, повторять нечего."""
    with _connect() as conn:
        person = _person(conn, CHAT)
        note = _failed(conn, person, hours_ago=30)
        conn.commit()

        stats = sender.retry_failed(conn, MOMENT, apply_changes=True,
                                    out=lambda *_: None)

        assert stats == {"failed": 1, "retried": 0, "skipped": 1}
        row = conn.execute(
            "select status, attempts, last_error from notification where id = %s",
            (note,)).fetchone()
        assert row["status"] == "skipped"
        assert row["attempts"] == 3
        assert "при пороге 24 ч" in row["last_error"]


def test_ошибка_не_настройки_помечается_skipped_с_причиной(client):
    with _connect() as conn:
        person = _person(conn, CHAT)
        note = _failed(conn, person, hours_ago=1, error=API_ERROR)
        conn.commit()

        stats = sender.retry_failed(conn, MOMENT, apply_changes=True,
                                    out=lambda *_: None)

        assert stats == {"failed": 1, "retried": 0, "skipped": 1}
        row = conn.execute(
            "select status, last_error from notification where id = %s",
            (note,)).fetchone()
        assert row["status"] == "skipped"
        assert "попытки исчерпаны" in row["last_error"]
        assert "message is too long" in row["last_error"]


def test_сухой_прогон_ничего_не_меняет(client):
    with _connect() as conn:
        person = _person(conn, CHAT)
        note = _failed(conn, person, hours_ago=3)
        conn.commit()

        stats = sender.retry_failed(conn, MOMENT, apply_changes=False,
                                    out=lambda *_: None)

        assert stats == {"failed": 1, "retried": 1, "skipped": 0}
        row = conn.execute(
            "select status, attempts, last_error from notification where id = %s",
            (note,)).fetchone()
        assert row["status"] == "failed"
        assert row["attempts"] == 3
        assert row["last_error"] == TOKEN_ERROR

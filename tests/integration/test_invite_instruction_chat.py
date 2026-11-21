"""Инструкция получателю уходит в чат тем же путём, что остальные уведомления.

E12 (docs/development/179 §5.4): экран Mini App текст не отправляет — строку
`kind = 'invite_instruction'` с готовым сообщением в `payload` ставит в очередь
`POST /webapp/admin/invite`, а отправляет общий `scripts/send_notifications.py`.
Здесь проверяется то, что видно только на живой очереди: ночью сообщение ждёт
08:00 (тихие часы — правило отправителя, а не выпуска кода), днём уходит
дословно тем же текстом, что бот присылает при выпуске из чата.

Обоснование: docs/development/179-e12-invite-instruction-chat.md, docs/development/174.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from scripts import send_notifications as sender
from services.shared.invite import INVITE_INSTRUCTION, INVITE_LINK
from tests.conftest import _test_dsn

TZ = timezone(timedelta(hours=4))            # Самара


def _connect():
    return psycopg.connect(_test_dsn(), row_factory=dict_row)


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _quiet_at_night(conn, participant_id: int) -> None:
    """Частные тихие часы: тест не зависит от общих настроек студии в `app_setting`."""
    conn.execute(
        "insert into notification_pref (participant_id, kinds_muted, quiet_from, quiet_to) "
        "values (%s, '{}', '20:00'::time, '08:00'::time)", (participant_id,))
    conn.commit()


def _queue(conn, recipient_id: int, moment: datetime) -> int:
    """Строка очереди такая, какой её ставит `POST /webapp/admin/invite`."""
    payload = json.dumps({"title": INVITE_LINK, "body": INVITE_INSTRUCTION},
                         ensure_ascii=False)
    return conn.execute(
        "insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at) "
        "values (%s, 'invite_instruction', %s, %s, %s) returning id",
        (recipient_id, payload, f"invite_instruction:{recipient_id}:test",
         moment - timedelta(minutes=5))).fetchone()["id"]


def _sender(calls: list[tuple[int, str]]):
    return lambda chat_id, text: (calls.append((chat_id, text)), (True, ""))[1]


def test_инструкция_ночью_ждёт_восьми_часов(client) -> None:
    """Тихие часы — правило отправителя: ночью выпуск не будит, сообщение ждёт утра."""
    calls: list[tuple[int, str]] = []
    moment = datetime(2026, 11, 21, 21, 0, tzinfo=TZ)

    with _connect() as conn:
        pid = _person(conn, 784010, "Инструкция Ночь")
        _quiet_at_night(conn, pid)
        note = _queue(conn, pid, moment)

        result = sender.deliver(conn, moment, apply_changes=True,
                                sender=_sender(calls), out=lambda *_: None)

        assert result["sent"] == 0 and result["postponed"] == 1
        assert calls == [], "ночью отправки быть не должно"
        wake = conn.execute("select scheduled_at from notification where id = %s",
                            (note,)).fetchone()["scheduled_at"]
        assert wake == datetime(2026, 11, 22, 8, 0, tzinfo=TZ)


def test_инструкция_днём_уходит_дословно_текстом_инструкции(client) -> None:
    """Уходит тем же путём, что остальные уведомления: один текст, без пересборки в боте."""
    calls: list[tuple[int, str]] = []
    moment = datetime(2026, 11, 21, 9, 0, tzinfo=TZ)

    with _connect() as conn:
        pid = _person(conn, 784011, "Инструкция День")
        _quiet_at_night(conn, pid)
        note = _queue(conn, pid, moment)

        result = sender.deliver(conn, moment, apply_changes=True,
                                sender=_sender(calls), out=lambda *_: None)

        assert result["sent"] == 1 and result["postponed"] == 0
        status = conn.execute("select status from notification where id = %s",
                              (note,)).fetchone()["status"]
        assert status == "sent"

    assert calls == [(784011, INVITE_LINK + "\n\n" + INVITE_INSTRUCTION)]


def test_отказ_отправки_не_теряет_инструкцию(client) -> None:
    """Сбой Telegram — запись остаётся в очереди до трёх попыток, а не пропадает."""
    moment = datetime(2026, 11, 21, 9, 0, tzinfo=TZ)

    with _connect() as conn:
        pid = _person(conn, 784012, "Инструкция Сбой")
        _quiet_at_night(conn, pid)
        note = _queue(conn, pid, moment)

        result = sender.deliver(conn, moment, apply_changes=True,
                                sender=lambda chat_id, text: (False, "HTTP 500"),
                                out=lambda *_: None)

        assert result["failed"] == 1 and result["sent"] == 0
        row = conn.execute("select status, attempts from notification where id = %s",
                           (note,)).fetchone()
        assert row["status"] == "scheduled" and row["attempts"] == 1

"""Лимит «3 сообщения в час» считается по адресату, а не по payload.chat_id (Ш-34, INT-002).

Дефект: `sent_last_hour()` сравнивал `payload->>'chat_id'`, а у уведомлений об
обращениях этого ключа в payload нет — счётчик всегда был 0, и за один прогон
27.10 ушло 27 сообщений двум людям вместо трёх.
"""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from app.person_key import make_key
from psycopg.rows import dict_row

from scripts import send_notifications as sender
from tests.conftest import _test_dsn

TZ = timezone(timedelta(hours=4))          # Самара
MOMENT = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
CHAT = 555001
LEGACY_CHAT = 555002


def _connect():
    return psycopg.connect(_test_dsn(), row_factory=dict_row)


def _person(conn, chat_id: int, name: str) -> int:
    return conn.execute("""
        insert into participant (tg_chat_id, display_name, person_key, role, status)
        values (%s, %s, %s, 'org', 'active') returning id""",
        (chat_id, name, make_key(name))).fetchone()["id"]


def _queue(conn, recipient_id: int, count: int, *, payload: dict | None = None,
           status: str = "scheduled", sent_at: datetime | None = None,
           prefix: str = "n") -> None:
    """Ставит count записей в очередь: без chat_id в payload, как у обращений."""
    for number in range(count):
        conn.execute("""
            insert into notification (recipient_id, kind, payload, dedupe_key,
                                      scheduled_at, status, sent_at)
            values (%s, 'feedback', %s, %s, %s, %s, %s)""",
            (recipient_id, psycopg.types.json.Jsonb(payload or {"title": "Обращение"}),
             f"{prefix}:{recipient_id}:{number}", MOMENT - timedelta(minutes=10),
             status, sent_at))


@pytest.fixture()
def stub(monkeypatch):
    """Telegram не дёргаем: считаем, кому и сколько ушло."""
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(sender, "send",
                        lambda chat_id, text: (calls.append((chat_id, text)), (True, ""))[1])
    return calls


def test_пять_записей_одному_человеку_уходят_три(client, stub):
    with _connect() as conn:
        person = _person(conn, CHAT, "Получатель")
        _queue(conn, person, 5, payload={"title": "Новое обращение"})
        conn.commit()

        result = sender.deliver(conn, MOMENT, apply_changes=True, out=lambda *_: None)

        assert result["sent"] == 3
        assert result["postponed"] == 2
        assert len(stub) == 3
        assert {chat for chat, _ in stub} == {CHAT}

        rows = conn.execute("select status, scheduled_at from notification "
                            "order by id").fetchall()
        assert [row["status"] for row in rows].count("sent") == 3
        assert [row["status"] for row in rows].count("scheduled") == 2
        # перенесённые ждут час, а не теряются
        for row in rows:
            if row["status"] == "scheduled":
                assert row["scheduled_at"] == MOMENT + timedelta(hours=1)


def test_старые_записи_с_chat_id_в_payload_тоже_считаются(client, stub):
    """У записей без recipient_id лимит продолжает работать по payload.chat_id."""
    with _connect() as conn:
        _queue(conn, None, 3, status="sent", sent_at=MOMENT - timedelta(minutes=20),
               payload={"title": "Старое", "chat_id": str(LEGACY_CHAT)}, prefix="old")
        person = _person(conn, LEGACY_CHAT, "Получатель")
        _queue(conn, person, 1, prefix="new")
        conn.commit()

        assert sender.sent_last_hour(conn, person, LEGACY_CHAT, MOMENT) == 3

        result = sender.deliver(conn, MOMENT, apply_changes=True, out=lambda *_: None)

        assert result["sent"] == 0
        assert result["postponed"] == 1
        assert stub == []


def test_чужой_час_не_считается(client, stub):
    """Отправленное больше часа назад лимит не занимает."""
    with _connect() as conn:
        person = _person(conn, CHAT, "Получатель")
        _queue(conn, person, 4, status="sent", sent_at=MOMENT - timedelta(hours=2),
               prefix="old")
        _queue(conn, person, 2, prefix="new")
        conn.commit()

        result = sender.deliver(conn, MOMENT, apply_changes=True, out=lambda *_: None)

        assert result["sent"] == 2
        assert result["postponed"] == 0

"""Обращения — только админу дайджестом (V-005, Ш-45).

Решение Виктора 28.10.2026: снять feedback.triage у роли org, не рассылать
обращения сразу, вместо этого раз в день в 11:00 класть админу одну запись
feedback_digest со списком открытых обращений.
"""

from datetime import datetime, timedelta, timezone

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from scripts import feedback_digest as digest
from tests.conftest import _test_dsn

ADMIN_CHAT = 123456789
MOMENT = datetime(2026, 10, 28, 11, 0, tzinfo=timezone(timedelta(hours=4)))
HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(ADMIN_CHAT)}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,%s) returning id",
        (chat_id, name, make_key(name), role, "active")).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _as(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _digest_conn():
    return psycopg.connect(_test_dsn(), row_factory=dict_row)


def test_обращение_не_рассылается_сразу_а_попадает_в_дайджест(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364910, "Автор Дайджеста")

    body = client.post("/tools/feedback", headers=_as(250364910),
                       json={"kind": "bug", "text": "Кнопка Расход не открывается"}).json()
    assert body["ok"] is True and body["notified"] == 0

    with _digest_conn() as conn:
        immediate = conn.execute(
            "select count(*) as n from notification where kind = %s",
            ("feedback_new",)).fetchone()["n"]
        assert immediate == 0, "мгновенная рассылка отменена решением V-005"

        rows = digest.plan(conn, MOMENT)
        assert [row["chat_id"] for row in rows] == [ADMIN_CHAT]
        assert "Автор Дайджеста" in rows[0]["body"]
        assert ("№{}".format(body["id"])) in rows[0]["body"]

        assert digest.apply(conn, rows) == 1
        assert digest.apply(conn, rows) == 0, "за день дайджест не дублируется"

        stored = conn.execute(
            "select kind, payload, dedupe_key from notification").fetchall()
        assert len(stored) == 1
        assert stored[0]["kind"] == "feedback_digest"
        assert stored[0]["payload"]["feedback_count"] == 1
        assert stored[0]["dedupe_key"] == "feedback-digest:2026-10-28"


def test_в_дайджест_попадают_только_открытые(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364912, "Автор Открытого")
        _person(conn, 250364913, "Автор Закрытого")

    opened = client.post("/tools/feedback", headers=_as(250364912),
                         json={"kind": "wish", "text": "Открытое пожелание"}).json()
    closed = client.post("/tools/feedback", headers=_as(250364913),
                         json={"kind": "wish", "text": "Закрытое пожелание"}).json()
    client.post("/tools/feedback/decide", headers=HEADERS,
                json={"feedback_id": closed["id"], "status": "done",
                      "resolution": "готово"})

    with _digest_conn() as conn:
        rows = digest.plan(conn, MOMENT)
        assert len(rows) == 1
        assert rows[0]["count"] == 1
        assert ("№{}".format(opened["id"])) in rows[0]["body"]
        assert ("№{}".format(closed["id"])) not in rows[0]["body"]



def test_дайджест_помечает_обращение_со_скриншотом(client):
    """К21: у обращения со скриншотом `has_attachment` истинно, и строка это говорит.

    Слово «со скриншотом» нужно админу до открытия обращения: скриншот дефекта часто
    и есть суть («чёрный экран»). Файл кладём прямо в схему — байты дайджесту не нужны.
    """
    with psycopg.connect(_test_dsn()) as conn:
        author = _person(conn, 250364914, "Автор Скриншота")
        with_shot = conn.execute(
            "insert into feedback (participant_id, kind, text) "
            "values (%s, 'bug', 'Чёрный экран') returning id", (author,)).fetchone()[0]
        without = conn.execute(
            "insert into feedback (participant_id, kind, text) "
            "values (%s, 'bug', 'Кнопка не нажимается') returning id", (author,)).fetchone()[0]
        file_id = conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key) values (%s,'IMG_0421.png','image/png',1800,"
            "'feedback','webapp','p' || %s || '/digest.png') returning id",
            (author, author)).fetchone()[0]
        conn.execute("insert into feedback_attachment (feedback_id, file_id, position)"
                     " values (%s, %s, 0)", (with_shot, file_id))
        conn.commit()

    with _digest_conn() as conn:
        items = {item["id"]: item for item in digest.open_feedback(conn)}
        marked = digest._line(items[with_shot])
        plain = digest._line(items[without])

    assert items[with_shot]["has_attachment"] is True
    assert items[without]["has_attachment"] is False
    assert "со скриншотом" in marked, marked
    assert "со скриншотом" not in plain, plain


def test_организатор_без_права_не_разбирает_и_не_получает(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364911, "Орг Без Права", "org")

    assert client.get("/tools/feedback", headers=_as(250364911)).status_code == 403

    with _digest_conn() as conn:
        chats = [row["tg_chat_id"] for row in digest.recipients(conn)]
        assert ADMIN_CHAT in chats
        assert 250364911 not in chats
        removed = conn.execute(
            "select 1 from rbac_role_capability where role = %s and capability = %s",
            ("org", "feedback.triage")).fetchone()
    assert removed is None, "решение V-005: у org больше нет feedback.triage"

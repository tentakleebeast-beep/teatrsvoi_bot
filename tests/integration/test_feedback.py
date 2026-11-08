"""Обратная связь: три входа и разбор (docs/development/57), журнал разборов (V-024)."""

import sys
from pathlib import Path

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

ROOT = Path(__file__).resolve().parents[2]

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _as(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}




def test_труппа_не_видит_чужую_очередь(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364901, "Труппа Очередь")
    assert client.get("/tools/feedback", headers=_as(250364901)).status_code == 403


def test_организатор_видит_очередь(client):
    body = client.get("/tools/feedback", headers=HEADERS).json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)
    assert "counts" in body


def test_разбор_обращения_уведомляет_автора(client):
    with psycopg.connect(_test_dsn()) as conn:
        author = _person(conn, 250364902, "Автор Разбор")

    created = client.post("/tools/feedback", headers=_as(250364902),
                          json={"kind": "wish", "text": "Хочу кнопку отмены"}).json()

    decided = client.post("/tools/feedback/decide", headers=HEADERS,
                          json={"feedback_id": created["id"], "status": "planned",
                                "resolution": "запланировали"}).json()
    assert decided["ok"] is True
    assert decided["status"] == "planned"

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from feedback where id = %s",
                              (created["id"],)).fetchone()[0]
        notices = conn.execute(
            "select count(*) from notification where dedupe_key = %s",
            (f"feedback:{created['id']}:planned",)).fetchone()[0]
        recipient = conn.execute("select recipient_id from notification where dedupe_key = %s",
                                 (f"feedback:{created['id']}:planned",)).fetchone()
    assert status == "planned"
    assert notices == 1 and recipient[0] == author


def test_разбор_пишет_журнал_и_умеет_не_уведомлять(client):
    """V-024: ответ хранится в журнале feedback_answer, notify=False молчит."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364904, "Автор Журнала")

    created = client.post("/tools/feedback", headers=_as(250364904),
                          json={"kind": "question", "text": "Как записать занятость?"}).json()

    decided = client.post("/tools/feedback/decide", headers=HEADERS,
                          json={"feedback_id": created["id"], "status": "seen",
                                "resolution": "V-024: как отвечать?",
                                "source": "agent", "question_id": "V-024",
                                "notify": False}).json()
    assert decided["ok"] is True and decided["notified"] == 0

    with psycopg.connect(_test_dsn()) as conn:
        answer = conn.execute(
            "select source, status, text, question_id from feedback_answer "
            "where id = %s", (decided["answer_id"],)).fetchone()
        notices = conn.execute(
            "select count(*) from notification where dedupe_key like %s",
            (f"feedback:{created['id']}:%",)).fetchone()[0]
    assert answer == ("agent", "seen", "V-024: как отвечать?", "V-024")
    assert notices == 0


def test_очередь_разбора_пропускает_разобранные(client):
    """V-024: обращение с записью в журнале не разбирается повторно."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import feedback_queue

    with psycopg.connect(_test_dsn()) as conn:
        author = _person(conn, 250364905, "Автор Очереди")
        unprocessed = conn.execute(
            "insert into feedback (participant_id, kind, text) "
            "values (%s, 'bug', 'кнопка не работает') returning id", (author,)).fetchone()[0]
        processed = conn.execute(
            "insert into feedback (participant_id, kind, text, status, resolution) "
            "values (%s, 'wish', 'хочу отчёт', 'done', 'готово') returning id",
            (author,)).fetchone()[0]
        conn.execute(
            "insert into feedback_answer (feedback_id, source, status, text) "
            "values (%s, 'agent', 'done', 'готово')", (processed,))
        conn.commit()
        ids = [row["id"] for row in feedback_queue.queue(conn, limit=10)]
    assert unprocessed in ids
    assert processed not in ids


def test_страница_формы_отдаётся(client):
    page = client.get("/webapp/feedback")
    assert page.status_code == 200
    assert "Обратная связь" in page.text


def test_обращение_из_mini_app(client):
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364903, "Из Мини Аппа")
    token = client.post("/webapp/auth", json={"init_data": init_data(250364903)}).json()["token"]

    created = client.post("/webapp/feedback", headers={"Authorization": f"Bearer {token}"},
                          json={"kind": "question", "text": "Как записать доход?"}).json()
    assert created["ok"] is True

    mine = client.get("/webapp/feedback/mine",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert mine["ok"] is True
    assert any(item["id"] == created["id"] for item in mine["data"])


def test_master_без_права_обратной_связи(client):
    """Экран 14: у мастера нет `feedback.send` — право приходит данными, отправка 403.

    Обоснование: db/init/031 (`feedback.send` только у admin/org/troupe),
    db/init/061 (у мастера права нет), docs/development/141 §3, карта t_4f0c64d6.
    """
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364906, "Мастер Обратной Связи", role="master")
        _person(conn, 250364907, "Труппа Обратной Связи")
    master = client.post("/webapp/auth", json={"init_data": init_data(250364906)}).json()["token"]
    troupe = client.post("/webapp/auth", json={"init_data": init_data(250364907)}).json()["token"]
    as_master = {"Authorization": f"Bearer {master}"}
    as_troupe = {"Authorization": f"Bearer {troupe}"}

    mine = client.get("/webapp/feedback/mine", headers=as_master).json()
    assert mine["ok"] is True
    assert mine["can_send"] is False, "мастеру обещано право отправки"

    denied = client.post("/webapp/feedback", headers=as_master,
                         json={"kind": "question", "text": "Можно отправить?"})
    assert denied.status_code == 403, denied.text
    assert "feedback.send" in denied.text

    allowed = client.get("/webapp/feedback/mine", headers=as_troupe).json()
    assert allowed["can_send"] is True, "у труппы право отправки есть (db/init/031)"


def test_очередь_без_закрытых_по_флагу(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364904, "Автор Закрытого")
    created = client.post("/tools/feedback", headers=_as(250364904),
                          json={"kind": "wish", "text": "Закрытое обращение"}).json()
    client.post("/tools/feedback/decide", headers=HEADERS,
                json={"feedback_id": created["id"], "status": "done",
                      "resolution": "готово"}).json()

    every = client.get("/tools/feedback?status=done&limit=100", headers=HEADERS).json()
    assert any(item["id"] == created["id"] for item in every["data"])

    open_only = client.get("/tools/feedback?open_only=true&limit=100", headers=HEADERS).json()
    assert all(item["id"] != created["id"] for item in open_only["data"])
    assert all(item["status"] in ("new", "seen", "planned") for item in open_only["data"])
    assert open_only["counts"].get("done", 0) >= 1

    closed = client.get("/tools/feedback?status=done&open_only=true", headers=HEADERS).json()
    assert closed["data"] == []


def test_просьба_о_доработке_сохраняется_видом_wish_fl(client):
    """FR-I1 (FR-E8 слито): просьба о новой функции — вид обращения, не отдельное действие.

    Отдельного маршрута и строки реестра действий нет: человек пользуется тем же
    `POST /tools/feedback`, меняется только вид. Поэтому на сервере проверяем
    хранение и заголовок такого вида, а не новое действие.
    """
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364905, "Автор Доработки")

    created = client.post("/tools/feedback", headers=_as(250364905),
                          json={"kind": "wish-fl",
                                "text": "Сделайте выгрузку бюджета в Excel"}).json()

    assert created["ok"] is True
    assert created["kind_title"] == "Просьба о доработке"

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select kind, status from feedback where id = %s",
                           (created["id"],)).fetchone()
    assert row == ("wish-fl", "new")

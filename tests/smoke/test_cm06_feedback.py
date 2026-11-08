"""СМ-6. Обратная связь: обращение → очередь → ответ → автор получил.

Роли: troupe, admin. Домен требований: F (обращения), E (доставка ответа).

Обоснование: FR-I1, FR-I2, FR-A8. Автор видит ответ в «Моих обращениях» — это проверяется
чтением; доставка сообщением в чат здесь не проверяется (нужен живой Telegram).
"""

SCENARIO = {
    "id": "СМ-6",
    "title": "Обратная связь: обращение → очередь → ответ → автор получил",
    "roles": ["troupe", "admin"],
    "domain": "F, E",
}


def test_cm06_feedback(journal, stand, people, troupe, admin, db):
    """Обращение пишется, попадает в очередь, ответ виден автору."""
    text = f"Смоук обращение {stand['marker']}: кнопка не отвечает"

    # 1. Обращение участника: строка feedback с автором (FR-I1).
    created = troupe.post("/tools/feedback", body={"kind": "bug", "text": text})
    feedback_id = (created.field("feedback") or {}).get("id") or created.field("id")
    row = db.execute("select id, participant_id, status from feedback where text = %s",
                     (text,)).fetchall()
    journal.check(
        "1. POST /tools/feedback", expected="код 200, одна строка feedback от автора",
        actual=f"код {created.code}, id={feedback_id}, строк {len(row)}",
        evidence=f"{created.evidence(100)}; feedback={[list(item) for item in row]}",
        ok=created.ok and len(row) == 1
        and row[0][1] == people["troupe"]["participant_id"],
        requirements=["FR-I1"])

    # 2. Очередь разбора видит обращение (FR-I2).
    queue = admin.get("/tools/feedback", params={"status": "new", "limit": 50})
    items = queue.field("data") or queue.field("items") or []
    ids = {item.get("id") for item in items}
    journal.check(
        "2. GET /tools/feedback?status=new (admin)",
        expected="обращение в очереди с автором и временем",
        actual=f"код {queue.code}, в очереди {len(items)}, наше: {feedback_id in ids}",
        evidence=queue.evidence(), ok=queue.ok and feedback_id in ids,
        requirements=["FR-I2"])

    # 3. Ответ организатора: статус и решение (FR-I2).
    decided = admin.post("/tools/feedback/decide", body={
        "feedback_id": feedback_id, "status": "done",
        "resolution": f"Смоук: проверено {stand['marker']}", "source": "human"})
    status_row = db.execute("select status, resolution from feedback where id = %s",
                            (feedback_id,)).fetchone()
    journal.check(
        "3. POST /tools/feedback/decide", expected="статус done и текст решения в базе",
        actual=f"код {decided.code}, статус={status_row[0] if status_row else None}, "
               f"решение={bool(status_row and status_row[1])}",
        evidence=f"{decided.evidence(100)}; feedback.status={status_row[0] if status_row else None}",
        ok=decided.ok and status_row is not None and status_row[0] == "done"
        and bool(status_row[1]),
        requirements=["FR-I2"])

    # 4. Автор видит ответ в «Моих обращениях» (FR-I1: «ответ автору»).
    mine = troupe.get("/tools/feedback/mine")
    mine_items = mine.field("data") or mine.field("items") or []
    found = next((item for item in mine_items if item.get("id") == feedback_id), {})
    journal.check(
        "4. GET /tools/feedback/mine (автор)", expected="своё обращение со статусом и ответом",
        actual=f"код {mine.code}, найдено={bool(found)}, статус={found.get('status')}, "
               f"ответ={bool(found.get('resolution') or found.get('answer'))}",
        evidence=mine.evidence(),
        ok=mine.ok and bool(found) and found.get("status") == "done",
        requirements=["FR-I1"])

    # 5. Ответ сохранён отдельной записью и связан с обращением (FR-I1).
    answers = db.execute(
        "select count(*) from feedback_answer where feedback_id = %s", (feedback_id,)).fetchone()[0]
    journal.check(
        "5. feedback_answer связан с обращением", expected="одна запись ответа",
        actual=f"записей {answers}",
        evidence=f"select count(*) from feedback_answer where feedback_id={feedback_id} → {answers}",
        ok=answers >= 1, requirements=["FR-I1"])

    # 6. Действие разбора попало в аудит с автором (FR-A8).
    audit = db.execute(
        "select count(*) from audit_event where action like %s and actor_id is not null",
        ("feedback.%",)).fetchone()[0]
    journal.check(
        "6. audit_event: feedback.* с автором", expected="есть запись с автором",
        actual=f"записей {audit}", evidence=f"audit_event action like feedback.% → {audit}",
        ok=audit > 0, requirements=["FR-A8"])

    # 7. Сообщение автору в чат (E-домен доставки) — не проверяется.
    journal.skip(
        "7. автору пришло сообщение с ответом",
        expected="сообщение в чате автора с текстом ответа",
        evidence="стенд не отправляет сообщения: адресаты — тестовые chat_id",
        cause="нужен живой Telegram", reason="доставку видно только в живом чате",
        requirements=["FR-I1"])

    journal.assert_passed()

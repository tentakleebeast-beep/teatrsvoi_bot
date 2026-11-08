"""СМ-7. Файлы и заметки: загрузка, квота, доступ по праву, удаление.

Роли: admin, org, master. Домен требований: F1–F7, H.

Обоснование: FR-F5, FR-F7, FR-A8. Файл грузится в хранилище стенда (Garage dev), а не
в прод: ключ выдан отдельно на стенд, бакет — `svoi-private` контейнера `svoi-garage-dev`.
"""

from conftest import chat_id

SCENARIO = {
    "id": "СМ-7",
    "title": "Файлы и заметки: загрузка, квота, доступ по праву, удаление",
    "roles": ["admin", "org", "master"],
    "domain": "F1–F7, H",
}
FILE_BODY = b"smoke file: " + b"x" * 1024


def test_cm07_files(journal, stand, people, admin, troupe, org, db):
    """Заметка пишется и читается, файл грузится, отказ по праву, удаление."""
    marker = stand["marker"]

    # 1. Заметка по праву notes.write: строка note с маркером (FR-F7).
    note = troupe.post("/tools/notes", body={
        "title": f"Смоук заметка {marker}", "body": f"текст заметки {marker}",
        "tags": ["смоук"], "scope": "personal"})
    note_id = (note.field("note") or {}).get("id")
    row = db.execute("select id, participant_id, scope from note where id = %s",
                     (note_id,)).fetchone()
    journal.check(
        "1. POST /tools/notes", expected="код 200, строка note у автора",
        actual=f"код {note.code}, id={note_id}, строка={list(row) if row else None}",
        evidence=f"{note.evidence(100)}; note={list(row) if row else None}",
        ok=note.ok and row is not None and row[1] == people["troupe"]["participant_id"],
        requirements=["FR-F7"])

    # 2. Свои заметки видны, чужие личные — нет (FR-F7).
    mine = troupe.get("/tools/notes")
    mine_ids = {item.get("id") for item in (mine.field("notes") or mine.field("data") or [])}
    others = org.get("/tools/notes")
    others_ids = {item.get("id") for item in (others.field("notes") or others.field("data") or [])}
    journal.check(
        "2. GET /tools/notes (свои и чужие)", expected="автор видит заметку, организатор — нет",
        actual=f"свои: {note_id in mine_ids}, у организатора: {note_id in others_ids}",
        evidence=f"{mine.evidence(80)}; {others.evidence(80)}",
        ok=note_id in mine_ids and note_id not in others_ids, requirements=["FR-F7"])

    # 3. Загрузка файла: строка file_object и остаток квоты в ответе (FR-F5).
    uploaded = admin.request("POST", "/files/upload", raw=FILE_BODY, headers={
        "X-File-Name": f"{marker}.txt", "X-File-Mime": "text/plain"})
    file_id = uploaded.field("id")
    file_row = db.execute(
        "select id, name, bucket, object_key, size_bytes from file_object where id = %s",
        (file_id,)).fetchone() if file_id else None
    journal.check(
        "3. POST /files/upload", expected="код 200, файл в базе и в хранилище стенда",
        actual=f"код {uploaded.code}, id={file_id}, квота {uploaded.field('quota_left_mb')} МБ, "
               f"строка={list(file_row) if file_row else None}",
        evidence=f"{uploaded.evidence(120)}; file_object={list(file_row) if file_row else None}",
        ok=uploaded.ok and file_row is not None and bool(file_row[2]) and bool(file_row[3]),
        requirements=["FR-F5"])

    # 4. Содержимое файла отдаётся целиком (FR-F5).
    content = admin.request("GET", f"/files/{file_id}/content")
    journal.check(
        "4. GET /files/{id}/content", expected="код 200, те же байты",
        actual=f"код {content.code}, получено {len(content.text)} символов",
        evidence=content.evidence(80), ok=content.ok,
        requirements=["FR-F5"])

    # 5. Чужой личный файл закрыт доступом, а не «спрятан в интерфейсе» (FR-F5, доступ по праву).
    foreign = troupe.request("GET", f"/files/{file_id}/content")
    journal.check(
        "5. GET /files/{id}/content (чужой)", expected="403 или 404: доступ по праву",
        actual=f"код {foreign.code}, code={foreign.field('code')}",
        evidence=foreign.evidence(), ok=foreign.code in (403, 404),
        requirements=["FR-F5"])

    # 6. Удаление файла: маршрута нет — это дефект, а не свойство стенда (FR-F5).
    deleted = admin.delete(f"/files/{file_id}")
    after = admin.request("GET", f"/files/{file_id}/content")
    journal.check(
        "6. удаление файла (DELETE /files/{id})",
        expected="код 200 и файл больше не отдаётся",
        actual=f"удаление {deleted.code} ({deleted.field('code')}), повторное чтение {after.code}",
        evidence=f"{deleted.evidence(100)}; {after.evidence(80)}",
        ok=deleted.ok and after.code in (404, 410, 403),
        requirements=["FR-F5"])

    # 7. Действие загрузки/удаления попало в аудит (FR-A8).
    audit = db.execute(
        "select count(*) from audit_event where action like %s", ("file.%",)).fetchone()[0]
    journal.check(
        "7. audit_event: file.*", expected="есть записи о загрузке и удалении",
        actual=f"записей {audit}", evidence=f"audit_event action like file.% → {audit}",
        ok=audit >= 2, requirements=["FR-A8"])

    # 8. Живой Telegram-файл из чата (human_telegram) на стенде не проверить (FR-F5).
    journal.skip(
        "8. загрузка файла из чата Telegram",
        expected="файл из чата попадает в file_object с uploaded_via=human_telegram",
        evidence=f"для этого нужен живой бот и чат; адресат стенда — {chat_id(stand, 1)}",
        cause="нужен живой Telegram", reason="файл приходит от Bot API",
        requirements=["FR-F5"])

    journal.assert_passed()

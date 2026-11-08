"""Удаление файла: мягкая метка, 404 после удаления, аудит и чужой файл (V-045).

Решение владельца 08.11.2026 (V-045, `docs/reports/74-questions.md`): требование
`docs/development/86` в силе — FR-K5 (кнопка «Удалить»), FR-M8 (удаление файла убирает
его из папок; удаление папки файлы не трогает), FR-L8 (удаляет только человек кнопкой).

Проверяются обещания маршрута `DELETE /files/{file_id}` и его оболочки
`DELETE /webapp/files/{file_id}`: файл после удаления не отдаётся ни метаданными, ни
содержимым, не виден в списке и в папке, связь с папкой снята, в аудите есть
`file.delete` без содержимого, а чужой личный файл вернуть нельзя — 404, как у
несуществующего (FR-K8).

Тесты живые: строки файлов и папок кладутся в БД напрямую, поэтому Garage не нужен.
Обоснование: FR-F5, FR-K5, FR-K8, FR-M8, FR-L8, `docs/development/86`.
"""

from __future__ import annotations

import json

import psycopg

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data


def _person(chat_id: int, role: str = "troupe") -> dict:
    """Участник с ролью: права считаются по `participant_role`, а не по колонке role."""
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') returning id",
            (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role)).fetchone()
        conn.execute(
            "insert into participant_role (participant_id, role) values (%s,%s)"
            " on conflict do nothing", (row[0], role))
        conn.commit()
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _file(chat_id: int, name: str = "смета.pdf") -> int:
    """Строка файла в БД: байты для этих проверок не нужны."""
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into file_object (participant_id, name, kind, object_key)"
            " select id, %s, 'text', %s from participant where tg_chat_id = %s returning id",
            (name, f"p{chat_id}/{name}", chat_id)).fetchone()
        conn.commit()
    return int(row[0])


def _into_folder(file_id: int, path: str = "Реквизит/сметы") -> int:
    """Положить файл в общую папку: удаление обязано снять связь (FR-M8)."""
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into folder (scope, name, path) values ('shared', %s, %s) returning id",
            (path.rsplit("/", 1)[-1], path)).fetchone()
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                     (file_id, row[0]))
        conn.commit()
    return int(row[0])


def _alive(file_id: int) -> bool:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select deleted_at from file_object where id = %s",
                           (file_id,)).fetchone()
    return row is not None and row[0] is None


def _audit(file_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select action, entity_type, entity_id, after from audit_event"
            " where action = 'file.delete' and entity_id = %s", (str(file_id),)).fetchall()
    out = []
    for row in rows:
        after = row[3] or {}
        if isinstance(after, str):
            after = json.loads(after)
        out.append({"action": row[0], "entity_type": row[1], "entity_id": row[2],
                    "after": after})
    return out


def _webapp_headers(client, chat_id: int) -> dict:
    from app import core

    core.BOT_TOKEN = BOT_TOKEN
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_владелец_удаляет_файл_и_тот_исчезает_отовсюду(client):
    auth = _person(561001, "admin")
    file_id = _file(561001)
    folder_id = _into_folder(file_id)

    assert client.get(f"/folders/{folder_id}/files", headers=auth).json()["count"] == 1

    response = client.delete(f"/files/{file_id}", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["id"] == file_id
    assert body["folders_detached"] == 1, "связь с папкой не снята (FR-M8)"

    assert not _alive(file_id), "строка не помечена deleted_at"
    assert client.get(f"/files/{file_id}", headers=auth).status_code == 404
    assert client.get(f"/files/{file_id}/content", headers=auth).status_code == 404
    assert all(row["id"] != file_id for row in client.get("/files", headers=auth).json()["data"])
    assert client.get(f"/folders/{folder_id}/files", headers=auth).json()["count"] == 0


def test_удаление_пишется_в_аудит_без_содержимого(client):
    auth = _person(561002, "admin")
    file_id = _file(561002, "партитура.pdf")

    assert client.delete(f"/files/{file_id}", headers=auth).status_code == 200

    events = _audit(file_id)
    assert len(events) == 1, f"событий file.delete: {len(events)}"
    event = events[0]
    assert event["entity_type"] == "file_object"
    assert set(event["after"]) == {"name", "folder_links", "soft"}, (
        f"в событии лишние поля: {sorted(event['after'])}")
    # Содержимого и ключа объекта в событии нет — только метаданные записи.
    assert "object_key" not in event["after"] and "content" not in event["after"]


def test_чужой_личный_файл_не_удаляется(client):
    owner = _person(561003, "admin")
    stranger = _person(561004, "admin")
    file_id = _file(561003)

    denied = client.delete(f"/files/{file_id}", headers=stranger)
    assert denied.status_code == 404, "чужой файл подтверждает своё существование"
    assert _alive(file_id), "чужой удалил мой файл"
    assert _audit(file_id) == []

    assert client.delete(f"/files/{file_id}", headers=owner).status_code == 200


def test_повторное_удаление_отвечает_404(client):
    auth = _person(561005, "admin")
    file_id = _file(561005)

    assert client.delete(f"/files/{file_id}", headers=auth).status_code == 200
    assert client.delete(f"/files/{file_id}", headers=auth).status_code == 404


def test_экран_удаляет_свой_файл_правом_files_access(client):
    """Кнопка на экране зовёт оболочку `/webapp/files/{id}` — тот же обработчик."""
    _person(561006, "admin")
    file_id = _file(561006, "фото.png")

    headers = _webapp_headers(client, 561006)
    response = client.delete(f"/webapp/files/{file_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert not _alive(file_id)
    events = _audit(file_id)
    assert events and events[0]["after"].get("via") == "webapp"


def test_роль_без_files_access_не_удаляет(client):
    _person(561007, "troupe")
    file_id = _file(561007)

    headers = _webapp_headers(client, 561007)
    denied = client.delete(f"/webapp/files/{file_id}", headers=headers)
    assert denied.status_code == 403, denied.text
    assert _alive(file_id)

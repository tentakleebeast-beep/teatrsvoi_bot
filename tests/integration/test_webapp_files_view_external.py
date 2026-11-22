"""Карточка ссылки на Drive и запрет скачивания (docs/development/192 §6.8–§6.9).

Ссылке нечего подписывать: ключа объекта нет. Поэтому `view` отдаёт `type='external'`
и `open_url`, не зовёт `presigned_view_url`, не трогает `cold.restore`, не обновляет
`last_access_at` и не отвечает 404 «файл не долетел» (сегодня `webapp_file_view` падал
именно на пустом `object_key`). Все три маршрута скачивания отвечают 409 `file_external`:
кнопка «Скачать» у ссылки не рисуется вовсе — маршрут, который гарантированно ругается,
хуже его отсутствия.

Обоснование: docs/development/189 §D7, AC-16.
"""

from __future__ import annotations

import json
import uuid

import psycopg

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import headers, participant

ADMIN_CHAT = 123456789
DRIVE_URL = "https://drive.google.com/file/d/external001/view"


def _drive_file(me: int, kind: str | None = None, name: str = "Афиши Пурги") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via,"
            " drive_file_id, drive_url, drive_path, storage, kind, description)"
            " values (%s,%s,'personal','human_drive',%s,%s,'Спектакли/Пурга','drive',%s,"
            " 'ссылка на папку афиш') returning id",
            (me, name, f"drive{uuid.uuid4().hex[:10]}", DRIVE_URL, kind)).fetchone()[0])


def _last_access(file_id: int):
    with psycopg.connect(_test_dsn()) as conn:
        return conn.execute("select last_access_at from file_object where id = %s",
                            (file_id,)).fetchone()[0]


def test_view_ссылки_отдаёт_внешний_адрес_и_ничего_не_подписывает(client, monkeypatch) -> None:
    """`type='external'`, `open_url` сохранён; подписи нет, `last_access_at` не меняется."""
    me = participant(ADMIN_CHAT)
    file_id = _drive_file(me, kind="folder")
    from app import cold, files_store

    def blow_up(*args, **kwargs):          # noqa: ANN002, ANN003 — подмена, а не код
        raise AssertionError("для ссылки подпись и холодный том звать нельзя")

    monkeypatch.setattr(files_store, "presigned_view_url", blow_up)
    monkeypatch.setattr(files_store, "presigned_url", blow_up)
    monkeypatch.setattr(cold, "restore", blow_up)

    answer = client.get(f"/webapp/files/{file_id}/view", headers=headers(client))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["type"] == "external"
    assert body["open_url"] == DRIVE_URL
    assert body["drive_path"] == "Спектакли/Пурга"
    assert body["kind"] == "folder" and body["kind_word"] == "папка"
    assert body["description"] == "ссылка на папку афиш"
    assert body["size_bytes"] is None
    for absent in ("url", "expires_in", "ext", "mime", "cold_restored"):
        assert absent not in body, f"у ссылки не должно быть поля {absent}"
    assert _last_access(file_id) is None, "last_access_at — отметка нашего объекта, а не Drive"


def test_три_маршрута_скачивания_отвечают_409(client, auth) -> None:
    """Скачивание ссылки: `/webapp/files/{id}/link`, `/files/{id}/content`, `/files/{id}/link`."""
    me = participant(ADMIN_CHAT)
    file_id = _drive_file(me)
    web = headers(client)
    for method, path, head in (
        ("get", f"/webapp/files/{file_id}/link", web),
        ("get", f"/files/{file_id}/content", auth),
        ("post", f"/files/{file_id}/link", auth),
    ):
        answer = getattr(client, method)(path, headers=head)
        assert answer.status_code == 409, f"{method} {path}: {answer.status_code}"
        body = json.loads(answer.text)
        assert body["code"] == "file_external"
        assert "открывается в Drive" in body["detail"]


def test_garage_файл_по_прежнему_подписывается(client, monkeypatch) -> None:
    """Контроль: у обычного файла `type='garage'` и подпись есть — правило не сломало его."""
    me = participant(ADMIN_CHAT)
    with psycopg.connect(_test_dsn()) as conn:
        file_id = int(conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key, bucket, kind, storage)"
            " values (%s,'Смета.pdf','application/pdf',10,'personal','webapp',%s,"
            " 'svoi-private','text','garage') returning id",
            (me, f"p{me}/{uuid.uuid4().hex}.pdf")).fetchone()[0])
    from app import files_store

    monkeypatch.setattr(files_store, "presigned_view_url",
                        lambda key, ttl=0: "https://files.test/signed")
    answer = client.get(f"/webapp/files/{file_id}/view", headers=headers(client))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["type"] == "garage"
    assert body["url"] == "https://files.test/signed"
    assert body["kind_word"] == "документ"

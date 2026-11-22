"""Название и описание файла: только владелец (V-124, docs/development/192 §6.4).

Описание — по желанию: поле с подсказкой при загрузке, пустое допустимо и хранится как
`null` (не `''`), правится владельцем через `/meta`. Чужой и несуществующий файл
отвечают одинаково — 404, не 403: иначе `FR-K8` нарушается перебором номеров.

Аудит `file.meta` несёт `before`/`after` только по изменённым полям — пустая правка
события не пишет, а «переименовал в то же имя» не выглядит изменением.

Обоснование: V-124, FR-K8, docs/development/189 §D2, AC-12.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import file_row, headers, participant

ADMIN_CHAT = 123456789


def _file(me: int, name: str = "Смета.pdf", description: str | None = None) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key, bucket, kind, storage, description)"
            " values (%s,%s,'application/pdf',10,'personal','webapp',%s,'svoi-private',"
            " 'text','garage',%s) returning id",
            (me, name, f"p{me}/{uuid.uuid4().hex}.pdf", description)).fetchone()[0])


def _audit(file_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "select action, before, after from audit_event"
            " where entity_type = 'file_object' and entity_id = %s and action = 'file.meta'"
            " order by id", (str(file_id),)).fetchall()
    return [dict(row) for row in rows]


def test_пустое_описание_хранится_как_null(client) -> None:
    """Поле необязательное: «ничего не написал» — это `null`, а не пустая строка."""
    me = participant(ADMIN_CHAT)
    file_id = _file(me)
    answer = client.post(f"/webapp/files/{file_id}/meta", json={"description": "   "},
                         headers=headers(client))
    assert answer.status_code == 200, answer.text
    assert answer.json()["description"] is None
    assert file_row(file_id)["description"] is None


def test_владелец_правит_описание_и_имя_аудит_помнит_оба_значения(client) -> None:
    """`/meta` меняет описание, аудит хранит `before`/`after` только изменённого."""
    me = participant(ADMIN_CHAT)
    file_id = _file(me, description="старое")
    answer = client.post(f"/webapp/files/{file_id}/meta",
                         json={"description": "  афиши и фото  "},
                         headers=headers(client))
    assert answer.status_code == 200, answer.text
    assert answer.json()["description"] == "афиши и фото"

    rows = _audit(file_id)
    assert len(rows) == 1
    assert rows[0]["before"]["description"] == "старое"
    assert rows[0]["after"]["description"] == "афиши и фото"

    same = client.post(f"/webapp/files/{file_id}/meta",
                       json={"description": "афиши и фото"}, headers=headers(client))
    assert same.status_code == 200
    assert len(_audit(file_id)) == 1, "правка без изменений не пишет событие"


def test_чужой_файл_отвечает_404(client) -> None:
    """Чужой и несуществующий неотличимы: 404, а не 403 (FR-K8)."""
    owner = participant(770301, name="Владелец")
    file_id = _file(owner)
    stranger = headers(client, 770302)

    for target in (file_id, file_id + 10_000):
        answer = client.post(f"/webapp/files/{target}/meta",
                             json={"description": "подмена"}, headers=stranger)
        assert answer.status_code == 404, answer.text
        assert answer.json()["code"] == "file_not_found"


def test_пустое_имя_и_длинное_описание_отказывают_словами(client) -> None:
    """Границы входа: пустое имя — 400 `file_name_empty`, >500 знаков — свой код."""
    me = participant(ADMIN_CHAT)
    file_id = _file(me)

    empty = client.post(f"/webapp/files/{file_id}/meta", json={"name": "   "},
                        headers=headers(client))
    assert empty.status_code == 400
    assert empty.json()["code"] == "file_name_empty"

    long = client.post(f"/webapp/files/{file_id}/meta", json={"description": "я" * 501},
                       headers=headers(client))
    assert long.status_code in (400, 422)
    if long.status_code == 400:
        assert long.json()["code"] == "file_description_too_long"


def test_описание_не_ищется(client) -> None:
    """Слово, которое есть только в описании, поиск не находит (§8.3, AC-12)."""
    me = participant(ADMIN_CHAT)
    file_id = _file(me, name="Афиша.png", description="слово-из-описания")
    answer = client.post(f"/webapp/files/{file_id}/meta",
                         json={"description": "слово-из-описания"},
                         headers=headers(client))
    assert answer.status_code == 200
    found = client.get("/webapp/files/search", params={"q": "слово-из-описания"},
                       headers=headers(client)).json()
    assert found["count"] == 0


def test_слово_и_чип_меняются_с_именем_а_kind_нет(client) -> None:
    """Переименование меняет слово (расширение в имени), но не `kind` и не `storage`."""
    me = participant(ADMIN_CHAT)
    file_id = _file(me, name="Смета.pdf")
    answer = client.post(f"/webapp/files/{file_id}/meta", json={"name": "заметки.md"},
                         headers=headers(client))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["kind"] == "text" and body["kind_word"] == "текст"
    assert file_row(file_id)["storage"] == "garage"

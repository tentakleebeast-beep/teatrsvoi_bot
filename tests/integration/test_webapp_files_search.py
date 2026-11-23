"""Поиск файлов: имя, папка, тип, место (FR-K17, docs/development/192 §6.6).

Сегодня поиск отвечает на «где афиша», «что в папке Пурга», «покажи все таблицы»:
`q` ищет по имени и по извлечённому тексту, `folder` — по нашему дереву и по `drive_path`,
`kind` — по одному списку расширений, `storage` — только ссылки или только Garage.

Видимость — правило `file_for_reader` в виде предиката списка: свой файл **или** файл общей
папки. Скриншот обращения не находится никогда, чужой закрытый личный — тоже (FR-K8).

Обоснование: FR-K17, V-125, docs/development/189 §D8, AC-15.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import headers, participant

ADMIN_CHAT = 123456789


def _folder(conn, *, scope: str, path: str, participant_id: int | None = None) -> int:
    name = path.rsplit("/", 1)[-1]
    row = conn.execute(
        "select id from folder where scope = %s and path = %s and deleted_at is null",
        (scope, path)).fetchone()
    if row:
        return int(row[0])
    parent = None
    if "/" in path:
        parent = _folder(conn, scope=scope, path=path.rsplit("/", 1)[0],
                         participant_id=participant_id)
    return int(conn.execute(
        "insert into folder (scope, participant_id, parent_id, name, path)"
        " values (%s,%s,%s,%s,%s) returning id",
        (scope, None if scope == "shared" else participant_id, parent, name, path)
    ).fetchone()[0])


def _file(conn, participant_id: int, name: str, *, kind: str | None = "text",
          folder_id: int | None = None, storage: str = "garage",
          drive_path: str | None = None, scope: str = "personal",
          text: str | None = None, size: int = 100) -> int:
    key = f"p{participant_id}/{uuid.uuid4().hex}.bin" if storage == "garage" else None
    row = conn.execute(
        "insert into file_object (participant_id, name, size_bytes, scope, uploaded_via,"
        " object_key, bucket, sha256, kind, storage, drive_file_id, drive_url, drive_path,"
        " description)"
        " values (%s,%s,%s,%s,'webapp',%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (participant_id, name, size, scope, key,
         "svoi-private" if storage == "garage" else None,
         "a" * 64 if storage == "garage" else None, kind, storage,
         f"drive{uuid.uuid4().hex[:10]}" if storage == "drive" else None,
         "https://drive.google.com/file/d/x/view" if storage == "drive" else None,
         drive_path, "описание только для человека")).fetchone()
    file_id = int(row[0])
    if folder_id is not None:
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                     (file_id, folder_id))
    if text:
        conn.execute("insert into file_text (file_id, body, status) values (%s,%s,'ok')",
                     (file_id, text))
    return file_id


@pytest.fixture()
def library(client):
    """Библиотека: свой Garage-документ с текстом, заметки, ссылка, общая папка, чужой, скрин."""
    me = participant(ADMIN_CHAT)
    stranger = participant(770201, name="Чужой Человек")
    with psycopg.connect(_test_dsn()) as conn:
        пурга = _folder(conn, scope="shared", path="Спектакли/Пурга")
        мои = _folder(conn, scope="personal", path="Личное/Мои документы", participant_id=me)
        чужая = _folder(conn, scope="personal", path="Личное/Мои документы",
                        participant_id=stranger)
        ids = {
            "смета": _file(conn, me, "Смета.pdf", folder_id=пурга, text="антракт и декорации"),
            "антракт": _file(conn, me, "антракт.docx", folder_id=пурга),
            "заметки": _file(conn, me, "заметки.md", folder_id=мои),
            "пд": _file(conn, me, "ПД-смета.pdf", folder_id=пурга),
            "ссылка": _file(conn, me, "Пурга — афиши", kind=None, storage="drive",
                            drive_path="Спектакли/Пурга"),
            "общая": _file(conn, me, "Афиша.png", kind="image", folder_id=пурга),
            "чужой": _file(conn, stranger, "чужой.pdf", folder_id=чужая),
            "скрин": _file(conn, me, "скрин.png", kind="image", scope="feedback"),
        }
        conn.commit()
    return ids


def _search(client, **params) -> dict:
    answer = client.get("/webapp/files/search", params=params, headers=headers(client))
    assert answer.status_code == 200, answer.text
    return answer.json()


def _names(body: dict) -> list[str]:
    return [row["name"] for row in body["data"]]


def test_q_по_тексту_и_по_имени_из_двух_знаков(client, library) -> None:
    """`q` ищет и по извлечённому тексту, и по имени — включая запрос «пд»."""
    assert "Смета.pdf" in _names(_search(client, q="антракт"))
    assert "ПД-смета.pdf" in _names(_search(client, q="пд")), \
        "запрос из двух знаков не нашёл файл по имени"


def test_folder_ищет_по_дереву_и_по_drive_path(client, library) -> None:
    """`folder=Спектакли` — по нашему пути; `folder=Пурга` — ещё и по `drive_path`."""
    parents = _names(_search(client, folder="Спектакли"))
    assert {"Смета.pdf", "ПД-смета.pdf", "Афиша.png"} <= set(parents)
    assert "Пурга — афиши" in _names(_search(client, folder="Пурга")), \
        "ссылка с drive_path не нашлась по папке"


def test_kind_делит_документы_и_текст(client, library) -> None:
    """`kind=документ` — pdf; `kind=текст` — md. Один файл не может быть в двух чипах."""
    documents = _names(_search(client, kind="документ"))
    assert "Смета.pdf" in documents and "заметки.md" not in documents
    texts = _names(_search(client, kind="текст"))
    assert "заметки.md" in texts and "Смета.pdf" not in texts


def test_storage_отдаёт_только_ссылки(client, library) -> None:
    """`storage=drive` — только ссылки: фильтр по колонке, а не по расширению."""
    names = _names(_search(client, storage="drive"))
    assert names == ["Пурга — афиши"]


def test_видимость_свой_общая_папка_и_ничего_чужого(client, library) -> None:
    """Свой файл и файл общей папки видны, чужой личный и скриншот обращения — нет."""
    names = _names(_search(client, q=""))
    assert "Смета.pdf" in names, "свой файл пропал"
    assert "Афиша.png" in names, "файл общей папки должен находиться: его открывает экран папки"
    assert "чужой.pdf" not in names, "чужой личный файл виден"
    assert "скрин.png" not in names, "скриншот обращения попал в поиск"


def test_совпадение_по_имени_выше(client, library) -> None:
    """Порядок: имя важнее текста — «антракт» сначала файл с таким именем, потом текст."""
    names = _names(_search(client, q="антракт"))
    assert names[0] == "антракт.docx", "совпадение по имени должно быть выше текстового"
    assert "Смета.pdf" in names, "файл, где «антракт» только в тексте, пропал"
    assert names.index("антракт.docx") < names.index("Смета.pdf")


def test_пустой_результат_и_неизвестный_тип(client, library) -> None:
    """Пусто — `data: []`; неизвестное слово — 400 `kind_unknown` словами (§6.6)."""
    empty = _search(client, q="нет-такого-слова-вообще")
    assert empty["data"] == [] and empty["count"] == 0

    answer = client.get("/webapp/files/search", params={"kind": "видео"},
                        headers=headers(client))
    assert answer.status_code == 400
    body = answer.json()
    assert body["code"] == "kind_unknown"
    assert "документ" in body["detail"]


def test_описание_в_поиск_не_входит(client, library) -> None:
    """Описание — для человека: слово из него ничего не находит (§8.3, AC-12)."""
    assert _names(_search(client, q="только для человека")) == []

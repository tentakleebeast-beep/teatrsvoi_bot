"""Поиск по тексту и теги со словарём (Ш-10, FR-K15, FR-K17).

Проверяем обещания, которые легко сломать: морфология русского («антракт» находит
«антракта»), чужие файлы не находятся, свободный тег становится черновиком и виден
только автору, а после утверждения — всем.
"""

from __future__ import annotations

import os
import pathlib
from urllib.parse import quote

import psycopg
import pytest

from tests.conftest import _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:                     # pragma: no cover
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят: STORAGE_ENDPOINT не задан")


def _person(chat_id: int, role: str = "troupe") -> dict:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') on conflict (tg_chat_id) do nothing",
            (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role))
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, %s from participant where tg_chat_id = %s on conflict do nothing",
            (role, chat_id))
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


@pytest.fixture()
def cleanup():
    keys: list[str] = []
    yield keys
    from app import files_store

    for key in keys:
        try:
            files_store.storage().delete_object(Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001
            pass


def _upload_text(client, headers: dict, name: str, body: str) -> dict:
    response = client.post("/files/upload", content=body.encode(),
                           headers={**headers, "X-File-Name": quote(name),
                                    "X-File-Mime": "text/plain"})
    assert response.status_code == 200, response.text
    return response.json()


def _object_key(file_id: int) -> str:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select object_key from file_object where id = %s",
                           (file_id,)).fetchone()
    return row[0] if row else ""


def test_поиск_находит_по_тексту_с_морфологией(client, cleanup) -> None:
    owner = _person(559001)
    saved = _upload_text(client, owner, "режиссура.md",
                         "Выход Пьеро после антракта, свет медленный")
    cleanup.append(_object_key(saved["id"]))

    for query in ("антракта", "антракт", "Пьеро"):
        found = client.get(f"/files?q={quote(query)}", headers=owner).json()
        assert any(row["id"] == saved["id"] for row in found["data"]), query


def test_пустой_результат_это_ответ_а_не_догадка(client) -> None:
    owner = _person(559002)
    _upload_text(client, owner, "сцена.md", "тишина и свет")
    found = client.get(f"/files?q={quote('балет')}", headers=owner).json()
    assert found["data"] == [] and found["count"] == 0


def test_чужой_файл_не_находится(client, cleanup) -> None:
    owner = _person(559003)
    stranger = _person(559004)
    saved = _upload_text(client, owner, "смета.md", "реквизит и костюмы Пурга")
    cleanup.append(_object_key(saved["id"]))

    found = client.get(f"/files?q={quote('реквизит')}", headers=stranger).json()
    assert all(row["id"] != saved["id"] for row in found["data"])


def test_тег_от_студийца_становится_черновиком(client) -> None:
    author = _person(559005)
    other = _person(559006)
    admin = _person(559007, "admin")

    created = client.post("/tags", json={"title": "Реквизит"}, headers=author).json()
    assert created["status"] == "draft" and created["created"] is True

    assert all(row["title"] != "Реквизит" for row in
               client.get("/tags", headers=other).json()["data"])
    assert any(row["title"] == "Реквизит" for row in
               client.get("/tags", headers=author).json()["data"])

    approved = client.post(f"/tags/{created['id']}/approve", headers=admin)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert any(row["title"] == "Реквизит" for row in
               client.get("/tags", headers=other).json()["data"])


def test_утверждать_теги_может_только_админ(client) -> None:
    author = _person(559008)
    created = client.post("/tags", json={"title": "Костюм"}, headers=author).json()
    denied = client.post(f"/tags/{created['id']}/approve", headers=author)
    assert denied.status_code == 403


def test_файл_находится_по_тегу(client, cleanup) -> None:
    owner = _person(559009, "admin")
    saved = _upload_text(client, owner, "опись.md", "список без ключевых слов")
    cleanup.append(_object_key(saved["id"]))
    tag = client.post("/tags", json={"title": "Пурга"}, headers=owner).json()

    applied = client.post(f"/files/{saved['id']}/tags", json={"tag_ids": [tag["id"]]},
                          headers=owner)
    assert applied.status_code == 200, applied.text

    found = client.get(f"/files?q={quote('Пурга')}", headers=owner).json()
    assert any(row["id"] == saved["id"] for row in found["data"])


def test_чужой_черновик_не_прицепить(client, cleanup) -> None:
    author = _person(559010)
    stranger = _person(559011)
    saved = _upload_text(client, stranger, "заметка.md", "текст")
    cleanup.append(_object_key(saved["id"]))
    draft = client.post("/tags", json={"title": "Черновик автора"}, headers=author).json()

    denied = client.post(f"/files/{saved['id']}/tags", json={"tag_ids": [draft["id"]]},
                         headers=stranger)
    assert denied.status_code == 409
    assert denied.json()["code"] == "draft_tag"


def test_теги_файла_видны_списком(client, cleanup) -> None:
    owner = _person(559012, "admin")
    saved = _upload_text(client, owner, "сводка.md", "содержимое")
    cleanup.append(_object_key(saved["id"]))
    first = client.post("/tags", json={"title": "Хозяйство"}, headers=owner).json()
    second = client.post("/tags", json={"title": "Афиши"}, headers=owner).json()
    client.post(f"/files/{saved['id']}/tags",
                json={"tag_ids": [first["id"], second["id"]]}, headers=owner)

    listed = client.get(f"/files/{saved['id']}/tags", headers=owner).json()
    assert {row["title"] for row in listed["data"]} == {"Хозяйство", "Афиши"}

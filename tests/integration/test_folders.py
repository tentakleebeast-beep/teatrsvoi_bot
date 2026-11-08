"""Дерево папок: корни, автосоздание папки спектакля, права и удаление без потери файлов (Ш-6).

Проверяется то, что обещано в docs/development/86 §3.1: корни — данные, папка спектакля
появляется сама при создании проекта (каким бы путём его ни создали), глубже пяти уровней
нельзя, а удаление папки никогда не уносит файлы с собой.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import _test_dsn

ROOTS = {"Хозяйство", "Соцсети", "Документы", "Спектакли"}


def _person(chat_id: int, role: str = "troupe", name: str | None = None) -> dict:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') on conflict (tg_chat_id) do nothing",
            (chat_id, name or f"Человек {chat_id}", f"человек-{chat_id}", role))
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, %s from participant where tg_chat_id = %s"
            " on conflict do nothing", (role, chat_id))
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _paths(client, auth, scope: str = "shared") -> dict[str, dict]:
    rows = client.get(f"/folders?scope={scope}", headers=auth).json()["data"]
    return {row["path"]: row for row in rows}


def test_корни_общих_папок_видны(client, auth) -> None:
    paths = _paths(client, auth)
    assert ROOTS <= set(paths)
    assert all(paths[root]["depth"] == 1 for root in ROOTS)


def test_спектакль_сам_заводит_папку_с_подпапками(client, auth) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into project (name, kind, status) values ('Тестовая Пурга','show','active')")

    paths = _paths(client, auth)
    assert "Спектакли/Тестовая Пурга" in paths
    show = paths["Спектакли/Тестовая Пурга"]
    assert show["depth"] == 2 and show["project_id"] is not None
    for part in ("тексты", "партитуры", "заметки", "музыка", "шумы"):
        assert f"Спектакли/Тестовая Пурга/{part}" in paths


def test_архивный_спектакль_папку_не_получает(client, auth) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into project (name, kind, status)"
                     " values ('Старый Спектакль','show','archive')")
    assert "Спектакли/Старый Спектакль" not in _paths(client, auth)


def test_организатор_создаёт_общую_папку_а_студиец_нет(client, auth) -> None:
    org = _person(556001, "org")
    troupe = _person(556002, "troupe")

    created = client.post("/folders", json={"name": "Реквизит", "scope": "shared"}, headers=org)
    assert created.status_code == 200, created.text
    assert created.json()["path"] == "Реквизит"

    denied = client.post("/folders", json={"name": "Чужое", "scope": "shared"}, headers=troupe)
    assert denied.status_code == 403
    assert denied.json()["code"] == "folder_forbidden"


def test_личная_папка_не_видна_другому(client, auth) -> None:
    owner = _person(556003, "troupe")
    stranger = _person(556004, "troupe")
    made = client.post("/folders", json={"name": "Мои шумы", "scope": "personal"}, headers=owner)
    assert made.status_code == 200, made.text

    assert "Мои шумы" in _paths(client, owner, "personal")
    assert "Мои шумы" not in _paths(client, stranger, "personal")


def test_имя_с_разделителем_отбивается(client, auth) -> None:
    response = client.post("/folders", json={"name": "Плохо/имя", "scope": "shared"}, headers=auth)
    assert response.status_code == 400
    assert response.json()["code"] == "folder_name_bad"


def test_шестой_уровень_не_создаётся(client, auth) -> None:
    parent = None
    for level in range(1, 6):
        body = {"name": f"уровень{level}", "scope": "shared"}
        if parent is not None:
            body["parent_id"] = parent
        created = client.post("/folders", json=body, headers=auth)
        assert created.status_code == 200, created.text
        parent = created.json()["id"]
    deep = client.post("/folders", json={"name": "слишком", "scope": "shared",
                                         "parent_id": parent}, headers=auth)
    assert deep.status_code == 409
    assert deep.json()["code"] == "folder_too_deep"


def test_переименование_переносит_детей(client, auth) -> None:
    """Путь ребёнка начинается с пути отца — иначе ребёнок «повисает» вне дерева."""
    first = client.post("/folders", json={"name": "Сцена", "scope": "shared"},
                        headers=auth).json()
    child = client.post("/folders", json={"name": "Записи", "scope": "shared",
                                          "parent_id": first["id"]}, headers=auth).json()
    assert child["path"] == "Сцена/Записи"

    renamed = client.post(f"/folders/{first['id']}/rename", json={"name": "Площадка"},
                          headers=auth)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["path"] == "Площадка"
    assert renamed.json()["renamed"] >= 1

    paths = _paths(client, auth)
    assert "Площадка/Записи" in paths and "Сцена/Записи" not in paths


def test_удаление_папки_не_удаляет_файлы(client, auth) -> None:
    folder = client.post("/folders", json={"name": "Разбор", "scope": "shared"},
                         headers=auth).json()
    sub = client.post("/folders", json={"name": "Октябрь", "scope": "shared",
                                        "parent_id": folder["id"]}, headers=auth).json()

    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (556005,'Хранитель Файла','хранитель','troupe','active') returning id"
        ).fetchone()[0]
        file_id = conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via, kind,"
            " size_bytes, sha256) values (%s,'смета.pdf','personal','bot','text',10,%s)"
            " returning id", (pid, "b" * 64)).fetchone()[0]
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                     (file_id, sub["id"]))

    removed = client.post(f"/folders/{folder['id']}/delete", headers=auth)
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["files_detached"] == 1
    assert body["folders_closed"] == 2          # вместе с вложенной

    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from file_object where id = %s",
                            (file_id,)).fetchone()[0] == 1
        assert conn.execute("select count(*) from file_folder where file_id = %s",
                            (file_id,)).fetchone()[0] == 0
    assert "Разбор" not in _paths(client, auth)


def test_чужую_личную_папку_не_переименовать(client) -> None:
    owner = _person(556006, "troupe")
    stranger = _person(556007, "troupe")
    made = client.post("/folders", json={"name": "Личное дело", "scope": "personal"},
                       headers=owner).json()
    response = client.post(f"/folders/{made['id']}/rename", json={"name": "Захват"},
                           headers=stranger)
    assert response.status_code == 403


@pytest.mark.parametrize("name", ["", "   "])
def test_пустое_имя_отбивается(client, auth, name) -> None:
    response = client.post("/folders", json={"name": name, "scope": "shared"}, headers=auth)
    assert response.status_code in (400, 422)

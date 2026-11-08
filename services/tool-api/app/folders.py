"""Папки: дерево, права и удаление без потери файлов (Ш-6, docs/development/86 §3.1).

Правила, которые здесь закреплены:
  * общие папки создают `admin` и `org`, читают все; личные — только владелец (FR-K10);
  * путь — ключ папки, поэтому имя чистится от «/» и не может повторяться (инвариант F9);
  * переименование меняет путь у всех вложенных папок, иначе дети повисают в воздухе;
  * удаление папки не удаляет файлы: они остаются в «Без папки» (FR-M8).
"""

from __future__ import annotations

import re

from . import files_store
from .errors import ApiError
from .external import SQL_ERRORS

SHARED_ROLES = {"admin", "org"}
SCOPES = ("shared", "personal")
MAX_NAME = 100
BAD_CHARS = re.compile(r"[/\\\x00]")


class FolderError(ApiError):
    """Ошибка операции: несёт машинный `code` и человеческий текст в конверте API.

    Наследуется от `ApiError`, поэтому до клиента доходит тем же телом ошибки, что и
    остальные отказы (docs/architecture/85), без переупаковки в ручках.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(status, message, code=code)
        self.message = message
        self.status = status


def roles_of(who: dict) -> set[str]:
    roles = set(who.get("roles") or [])
    roles.add(who.get("role") or "")
    return {role for role in roles if role}


def clean_name(raw: str) -> str:
    """Имя папки: без «/», без пустоты, не длиннее разумного.

    «/» — разделитель пути: попав в имя, он создаёт папку, которой нет в дереве.
    """
    name = " ".join((raw or "").split())
    if not name:
        raise FolderError("folder_name_empty", "Название папки не может быть пустым.")
    if BAD_CHARS.search(name):
        raise FolderError("folder_name_bad", "В названии нельзя использовать «/» и «\\».")
    if name in (".", ".."):
        raise FolderError("folder_name_bad", "Такое название не подходит.")
    if len(name) > MAX_NAME:
        raise FolderError("folder_name_bad", f"Название длиннее {MAX_NAME} символов.")
    return name


def can_manage(who: dict, folder: dict) -> bool:
    """Общую папку правят admin и org, личную — только её владелец."""
    if folder.get("scope") == "shared":
        return bool(roles_of(who) & SHARED_ROLES)
    return int(folder.get("participant_id") or 0) == int(who.get("id") or 0)


def get(conn, folder_id: int) -> dict:
    """Папка по номеру. Отдельная обёртка, чтобы не тянуть приватную функцию из других модулей."""
    return _folder(conn, folder_id)


def _folder(conn, folder_id: int) -> dict:
    row = conn.execute(
        "select id, scope, participant_id, parent_id, name, path, depth, project_id"
        "  from folder where id = %s and deleted_at is null", (folder_id,)).fetchone()
    if row is None:
        raise FolderError("folder_not_found", "Папка не найдена.", 404)
    return row


def tree(conn, scope: str, participant_id: int) -> list[dict]:
    """Дерево папок: общее — всем, личное — только своё."""
    if scope not in SCOPES:
        raise FolderError("folder_scope_unknown", "Неизвестный тип папки.")
    rows = conn.execute(
        "select id, parent_id, name, path, depth, project_id, created_at"
        "  from folder"
        " where scope = %s and deleted_at is null"
        "   and (%s = 'shared' or participant_id = %s)"
        " order by path",
        (scope, scope, participant_id)).fetchall()
    return [dict(row) for row in rows]


def create(conn, who: dict, name: str, scope: str, parent_id: int | None) -> dict:
    """Создать папку. Родитель задаёт и путь, и права, и тип."""
    if scope not in SCOPES:
        raise FolderError("folder_scope_unknown", "Неизвестный тип папки.")
    name = clean_name(name)

    parent = None
    if parent_id is not None:
        parent = _folder(conn, parent_id)
        if parent["scope"] != scope:
            raise FolderError("folder_scope_mismatch", "Родительская папка другого типа.")

    if scope == "shared":
        if not roles_of(who) & SHARED_ROLES:
            raise FolderError("folder_forbidden", "Общие папки создают администратор и организаторы.", 403)
        participant_id = None
    else:
        participant_id = int(who.get("id") or 0)
        if not participant_id:
            raise FolderError("folder_forbidden", "Личные папки доступны только участнику.", 403)

    path = f"{parent['path']}/{name}" if parent else name
    existing = conn.execute(
        "select id from folder where scope = %s and path = %s and deleted_at is null"
        "   and (participant_id is not distinct from %s)",
        (scope, path, participant_id)).fetchone()
    if existing:
        raise FolderError("folder_exists", "Папка с таким названием уже есть.", 409)

    try:
        row = conn.execute(
            "insert into folder (scope, participant_id, parent_id, name, path)"
            " values (%s,%s,%s,%s,%s) returning id, parent_id, name, path, depth",
            (scope, participant_id, parent_id, name, path)).fetchone()
    except SQL_ERRORS as exc:                      # триггер глубины и уникальность
        message = str(exc)
        if "5 уровней" in message:
            raise FolderError("folder_too_deep", "Глубже 5 уровней нельзя: не найдёте.", 409) from exc
        if "unique" in message or "duplicate key" in message:
            raise FolderError("folder_exists", "Папка с таким названием уже есть.", 409) from exc
        raise
    return dict(row)


def rename(conn, who: dict, folder_id: int, name: str) -> dict:
    """Переименовать папку вместе со всем поддеревом: путь ребёнка начинается с пути отца."""
    folder = _folder(conn, folder_id)
    if not can_manage(who, folder):
        raise FolderError("folder_forbidden", "Это не ваша папка.", 403)
    name = clean_name(name)
    if name == folder["name"]:
        return {"id": folder_id, "name": name, "path": folder["path"], "renamed": 0}

    old_path = folder["path"]
    new_path = f"{old_path.rsplit('/', 1)[0]}/{name}" if "/" in old_path else name
    try:
        updated = conn.execute(
            """
            with recursive sub as (
                select id, path from folder where id = %(id)s
                union all
                select f.id, f.path from folder f join sub s on f.parent_id = s.id
                 where f.deleted_at is null
            )
            update folder f
               set path = %(new)s || substr(f.path, length(%(old)s) + 1),
                   name = case when f.id = %(id)s then %(name)s else f.name end
              from sub
             where f.id = sub.id
            returning f.id
            """,
            {"id": folder_id, "new": new_path, "old": old_path, "name": name}).fetchall()
    except SQL_ERRORS as exc:                      # коллизия путей
        if "unique" in str(exc) or "duplicate key" in str(exc):
            raise FolderError("folder_exists", "Папка с таким названием уже есть.", 409) from exc
        raise
    return {"id": folder_id, "name": name, "path": new_path,
            "renamed": max(len(updated) - 1, 0)}


def set_order(conn, who: dict, folder_id: int, file_ids: list[int]) -> dict:
    """Порядок треков в папке. Один обработчик на оба входа: чат и экран.

    Права те же, что у переименования: порядок задаёт тот, кто папкой управляет.
    Раньше проверка была скопирована в двух маршрутах и могла разъехаться.
    """
    folder = _folder(conn, folder_id)
    if not can_manage(who, folder):
        raise FolderError("folder_forbidden", "Порядок задаёт владелец папки.", 403)
    updated = files_store.set_order(conn, folder_id, file_ids)
    return {"folder_id": folder_id, "path": folder["path"], "updated": updated}


def remove(conn, who: dict, folder_id: int) -> dict:
    """Закрыть папку. Файлы остаются: связь снимается, они видны в «Без папки»."""
    folder = _folder(conn, folder_id)
    if not can_manage(who, folder):
        raise FolderError("folder_forbidden", "Это не ваша папка.", 403)

    closed = conn.execute(
        """
        with recursive sub as (
            select id from folder where id = %s
            union all
            select f.id from folder f join sub s on f.parent_id = s.id
             where f.deleted_at is null
        )
        update folder set deleted_at = now()
         where id in (select id from sub) and deleted_at is null
        returning id
        """, (folder_id,)).fetchall()
    detached = conn.execute(
        """
        with recursive sub as (
            select id from folder where id = %s
            union all
            select f.id from folder f join sub s on f.parent_id = s.id
        )
        delete from file_folder where folder_id in (select id from sub) returning file_id
        """, (folder_id,)).fetchall()
    return {"id": folder_id, "folders_closed": len(closed), "files_detached": len(detached)}

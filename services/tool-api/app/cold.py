"""Холодное хранение: файлы, к которым давно не обращались (Ш-11, FR-K14).

Холод — это второй том у того же провайдера, а не ещё один «умный» слой: файл переезжает
в обычный каталог на другом диске и удаляется из горячего бакета. В базе остаётся запись
с `cold_at` и тот же `object_key` — по нему файл возвращается обратно.

Две честные оговорки, которые нужно держать в голове:
  * второй том живёт на том же сервере: это экономия места, а не резервная копия
    (бэкап холодного — отдельная задача, NFR-K4);
  * восстановление синхронное: файл в 100 МБ вернётся за секунды, а не «через сутки»,
    потому что это локальный диск, а не лента.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from psycopg.rows import dict_row

from .external import STORAGE_ERRORS

log = logging.getLogger("cold")

DEFAULT_DAYS = 180
DEFAULT_LIMIT = 200


def _fetch(conn, sql: str, params: tuple = ()) -> list[dict]:
    """Своя фабрика строк: модуль не должен зависеть от того, как настроен вызов.

    Скрипт ротации, API и тесты открывают соединения по-разному (dict_row или кортежи);
    читать колонки по имени — единственный способ не спотыкаться об это.
    """
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def cold_dir() -> Path:
    return Path(os.getenv("STORAGE_COLD_DIR", "/opt/svoi/cold"))


def _path_for(key: str) -> Path:
    """Путь на холодном томе повторяет ключ объекта: `p12/<uuid>.mp3`."""
    return cold_dir() / key


def is_cold(row: dict) -> bool:
    return bool(row.get("cold_at"))


def status(conn) -> dict:
    """Сколько лежит горячим, сколько холодным и сколько занимает."""
    row = _fetch(
        conn,
        "select count(*) filter (where cold_at is null) as hot_files,"
        "       count(*) filter (where cold_at is not null) as cold_files,"
        "       coalesce(sum(size_bytes) filter (where cold_at is null), 0) as hot_bytes,"
        "       coalesce(sum(size_bytes) filter (where cold_at is not null), 0) as cold_bytes"
        "  from file_object where deleted_at is null")[0]
    return row | {"cold_dir": str(cold_dir()),
                        "cold_dir_exists": cold_dir().is_dir()}


def move_to_cold(conn, days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT) -> dict:
    """Перенести на холод файлы, не запрошенные `days` дней.

    Считаем по `last_access_at`, а не по дате загрузки: файл, который открывают каждую
    неделю, остаётся горячим, даже если загружен год назад.
    """
    from . import files_store

    rows = _fetch(
        conn,
        "select id, name, object_key, size_bytes from file_object"
        " where deleted_at is null and cold_at is null and object_key is not null"
        "   and coalesce(last_access_at, created_at) < now() - make_interval(days => %s)"
        " order by coalesce(last_access_at, created_at) limit %s",
        (int(days), int(limit)))

    moved = 0
    total = 0
    for row in rows:
        key = row["object_key"]
        target = _path_for(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            files_store.storage().download_file(
                files_store.bucket_private(), key, str(target))
            files_store.storage().delete_object(
                Bucket=files_store.bucket_private(), Key=key)
        except STORAGE_ERRORS as exc:             # один сбой хранилища не останавливает ротацию
            log.error("не удалось перенести в холод %s: %s", key, exc)
            target.unlink(missing_ok=True)
            continue
        conn.execute("update file_object set cold_at = now() where id = %s", (row["id"],))
        moved += 1
        total += int(row["size_bytes"] or 0)
    return {"moved": moved, "bytes": total, "checked": len(rows)}


def restore(conn, file_id: int) -> bool:
    """Вернуть файл из холода в горячий бакет. True — файл теперь горячий."""
    from . import files_store

    found = _fetch(conn, "select id, object_key, cold_at from file_object where id = %s",
                   (file_id,))
    row = found[0] if found else None
    if row is None or row["cold_at"] is None or not row["object_key"]:
        return False
    path = _path_for(row["object_key"])
    if not path.is_file():
        # файла нет ни в бакете, ни на холодном томе — это потеря, о ней надо кричать
        raise FileNotFoundError(f"нет холодной копии {row['object_key']}")
    with path.open("rb") as handle:
        files_store.storage().put_object(
            Bucket=files_store.bucket_private(), Key=row["object_key"], Body=handle)
    path.unlink(missing_ok=True)
    _prune_empty_dirs(path.parent)
    conn.execute("update file_object set cold_at = null, last_access_at = now()"
                 " where id = %s", (file_id,))
    return True


def _prune_empty_dirs(directory: Path) -> None:
    """Убрать опустевшие каталоги: иначе холод зарастает папками без файлов."""
    root = cold_dir()
    current = directory
    while root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def free_space() -> dict:
    """Сколько осталось на холодном томе — до того, как туда поедут файлы."""
    root = cold_dir()
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    return {"total": usage.total, "used": usage.used, "free": usage.free}

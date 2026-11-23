"""Личное место считает только личные байты (V-125, docs/development/192 §5).

Правило одно на чтение и вставку: в личные 2 ГБ входят только мои живые Garage-файлы,
которые не скриншот обращения и не лежат ни в одной общей папке. Файл в общей папке,
ссылка на Drive (даже с `size_bytes` 5 ГБ) и скриншот обращения личного места не занимают.

Почему по папке, а не по колонке `scope`: файл можно перенести и положить в две папки
сразу, а `scope` при переносе не меняется — «общий» по ней стал бы ложью после первого
переноса. Проверка на перенос в обе стороны — здесь же.

Обоснование: FR-K14, V-125, docs/development/192 §5.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from app import files_store
from psycopg.rows import dict_row

from tests.conftest import ADMIN_CHAT, _test_dsn

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GB = 1024 ** 3


def _me() -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute("select id from participant where tg_chat_id = %s",
                                (ADMIN_CHAT,)).fetchone()[0])


def _shared_folder(name: str = "Спектакли") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select id from folder where scope = 'shared' and path = %s"
                           " and deleted_at is null", (name,)).fetchone()
        if row:
            return int(row[0])
        return int(conn.execute("select ensure_shared_folder(null, %s, %s)",
                                (name, name)).fetchone()[0])


def _personal_folder(pid: int) -> int:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        folder_id, _path = files_store.ensure_personal_folder(conn, pid, "text")
        conn.commit()
        return int(folder_id)


def _file(pid: int, *, size: int, storage: str = "garage", scope: str = "personal",
          folder_id: int | None = None, name: str = "файл.pdf") -> int:
    key = f"p{pid}/{uuid.uuid4().hex}.pdf" if storage == "garage" else None
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into file_object (participant_id, name, size_bytes, scope, uploaded_via,"
            " object_key, bucket, kind, storage, drive_file_id, drive_url)"
            " values (%s,%s,%s,%s,'webapp',%s,'svoi-private','text',%s,%s,%s) returning id",
            (pid, name, size, scope, key, storage,
             None if storage == "garage" else f"drive{uuid.uuid4().hex[:8]}",
             None if storage == "garage" else "https://drive.google.com/file/d/x/view")
        ).fetchone()[0]
        if folder_id is not None:
            conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                         (row, folder_id))
        conn.commit()
        return int(row)


def _move(file_id: int, folder_id: int | None) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from file_folder where file_id = %s", (file_id,))
        if folder_id is not None:
            conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                         (file_id, folder_id))
        conn.commit()


def _used(pid: int) -> int:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return files_store.used_bytes(conn, pid)


def test_в_общей_папке_и_ссылка_и_скриншот_не_в_личном_месте(client, test_db) -> None:
    """Три «не личных» случая сразу: общая папка, ссылка 5 ГБ, скриншот обращения."""
    pid = _me()
    shared = _shared_folder()
    assert _used(pid) == 0

    personal = _file(pid, size=100 * 1024 ** 2)                 # 100 МБ — личное
    _file(pid, size=200 * 1024 ** 2, folder_id=shared)          # общая папка — не личное
    _file(pid, size=5 * GB, storage="drive")                    # ссылка — не личное
    _file(pid, size=8 * 1024 ** 2, scope="feedback")            # скриншот — не личное

    assert _used(pid) == 100 * 1024 ** 2, "в личное место попал не личный файл"
    assert personal


def test_перенос_в_общую_папку_выводит_из_квоты_и_возвращает(client, test_db) -> None:
    """Истина о «общий/личный» — папка: перенос в обе стороны меняет счётчик."""
    pid = _me()
    shared = _shared_folder()
    personal_folder = _personal_folder(pid)
    file_id = _file(pid, size=100 * 1024 ** 2)
    _move(file_id, personal_folder)
    assert _used(pid) == 100 * 1024 ** 2

    _move(file_id, shared)
    assert _used(pid) == 0, "файл в общей папке продолжал занимать личное место"

    _move(file_id, personal_folder)
    assert _used(pid) == 100 * 1024 ** 2, "перенос обратно не вернул файл в квоту"


def test_вставка_в_общую_папку_проходит_при_заполненном_личном_месте(client, test_db,
                                                                  monkeypatch) -> None:
    """Проверка при вставке считает по тому же правилу: 300 МБ в общую папку пройдут."""
    monkeypatch.setattr(files_store, "delete_object", lambda key: None)
    pid = _me()
    shared = _shared_folder()
    _file(pid, size=files_store.QUOTA_BYTES)                    # личное место занято полностью
    assert _used(pid) == files_store.QUOTA_BYTES

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        saved = files_store.save_committed(
            conn, pid, "Афиша.png", "image/png", 300 * 1024 ** 2, None,
            f"p{pid}/shared.png", PNG, folder_id=shared)
        conn.commit()

    assert saved.duplicate is False
    assert saved.folder_id == shared
    with psycopg.connect(_test_dsn()) as conn:
        scope = conn.execute("select scope from file_object where id = %s",
                             (saved.id,)).fetchone()[0]
    assert scope == "shared", "файл в общей папке получает scope общей папки"
    assert _used(pid) == files_store.QUOTA_BYTES, "вставка изменила личное место"


def test_вставка_в_личную_папку_при_полном_месте_отказывает(client, test_db, monkeypatch) -> None:
    """Контроль: то же самое без общей папки — отказ с точной цифрой (FR-K14)."""
    monkeypatch.setattr(files_store, "delete_object", lambda key: None)
    pid = _me()
    _file(pid, size=files_store.QUOTA_BYTES)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        with pytest.raises(files_store.FileRejected) as caught:
            files_store.save_committed(
                conn, pid, "Афиша.png", "image/png", 1024, None,
                f"p{pid}/private.png", PNG)
    assert caught.value.code == "file_quota_exceeded"



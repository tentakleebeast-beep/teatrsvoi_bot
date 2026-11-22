"""Два расположения файла: инварианты F11–F13 и backfill миграции 072 (docs/development/192 §3).

Что проверяется:
* F11 — garage хранит байты, а не ссылку;
* F12 — ссылка не держит байтов;
* F13 — папка Drive бывает только ссылкой (V-123);
* backfill: строка-заглушка (`drive_file_id` без `object_key`) переходит в `drive`
  с собранным адресом, и после этого F11 и F12 зелёные;
* мягко удалённая строка без ключа F11 не краснит: она не видна ни в одном списке, и F2,
  из которого растёт инвариант, считает так же.

Обоснование: FR-K1, FR-K3, V-123, docs/development/189 §D1, AC-1…AC-5.
"""

from __future__ import annotations

import pathlib

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "init" / "072_files_two_locations.sql"


def _person(conn, chat_id: int) -> int:
    return conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s, %s, %s, 'troupe', 'active') returning id",
        (chat_id, f"Тест {chat_id}", f"test-{chat_id}")).fetchone()[0]


def _garage(conn, pid: int, *, key: str | None = "p1/a.pdf",
            bucket: str | None = "svoi-private", drive_url: str | None = None) -> int:
    return conn.execute(
        "insert into file_object (participant_id, name, mime, size_bytes, scope, uploaded_via,"
        " object_key, bucket, sha256, kind, storage, drive_url)"
        " values (%s,'Смета.pdf','application/pdf',10,'personal','webapp',%s,%s,%s,'text',"
        " 'garage',%s) returning id",
        (pid, key, bucket, "a" * 64, drive_url)).fetchone()[0]


def _check(conn, code: str) -> bool:
    return conn.execute("select ok from v_file_checks where code = %s", (code,)).fetchone()[0]


def test_пустая_база_и_все_три_инварианта_зелёные(test_db: str, client) -> None:
    """F11–F13 на пустой базе: миграция не оставила противоречий сама с собой."""
    with psycopg.connect(test_db) as conn:
        assert _check(conn, "F11")
        assert _check(conn, "F12")
        assert _check(conn, "F13")


def test_garage_без_ключа_краснит_f11(test_db: str, client) -> None:
    """Garage-строка без `object_key` — это ссылка, притворившаяся файлом."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780001)
        _garage(conn, pid, key=None)
        assert not _check(conn, "F11")


def test_garage_с_адресом_drive_краснит_f11(test_db: str, client) -> None:
    """Байты и ссылка вместе — два ответа на вопрос «где файл»."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780002)
        _garage(conn, pid, drive_url="https://drive.google.com/file/d/x/view")
        assert not _check(conn, "F11")


def test_ссылка_с_ключом_краснит_f12(test_db: str, client) -> None:
    """Ссылка на Drive с нашим `object_key` — это уже не ссылка."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780003)
        conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via,"
            " drive_file_id, drive_url, storage, object_key, bucket, kind)"
            " values (%s,'ссылка','personal','human_drive','driveFILE01',"
            " 'https://drive.google.com/file/d/driveFILE01/view','drive','p1/a.pdf',"
            " 'svoi-private', null)", (pid,))
        assert not _check(conn, "F12")


def test_ссылка_без_адреса_краснит_f12(test_db: str, client) -> None:
    """Перенос в `drive` без адреса: строка знает, что она ссылка, но не знает куда."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780004)
        conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via,"
            " drive_file_id, storage, kind)"
            " values (%s,'ссылка','personal','human_drive','driveFILE02','drive',null)",
            (pid,))
        assert not _check(conn, "F12")


def test_папка_в_garage_краснит_f13(test_db: str, client) -> None:
    """V-123: `kind='folder'` — только ссылка; папка с байтами Garage невозможна."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780005)
        _garage(conn, pid, key="p1/folder.pdf")
        conn.execute("update file_object set kind = 'folder' where participant_id = %s", (pid,))
        assert not _check(conn, "F13")


def test_мягко_удалённая_строка_без_ключа_f11_не_краснит(test_db: str, client) -> None:
    """Удалённая строка не видна ни в одном списке — и инвариант считает так же (§3.3)."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780006)
        row_id = _garage(conn, pid, key=None)
        conn.execute("update file_object set deleted_at = now() where id = %s", (row_id,))
        assert _check(conn, "F11")


def test_kind_folder_проходит_а_video_нет(test_db: str, client) -> None:
    """`folder` добавлен в `kind`, `video` по-прежнему отвергается ограничением."""
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780007)
        conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via,"
            " drive_file_id, drive_url, storage, kind)"
            " values (%s,'Пурга','personal','human_drive','folderID001',"
            " 'https://drive.google.com/drive/folders/folderID001','drive','folder')",
            (pid,))
        assert _check(conn, "F13")
        with pytest.raises(psycopg.errors.CheckViolation, match="file_object_kind_check"):
            conn.execute(
                "insert into file_object (participant_id, name, scope, uploaded_via,"
                " object_key, bucket, kind) values (%s,'ролик','personal','webapp',"
                " 'p1/video.mp4','svoi-private','video')", (pid,))


def test_backfill_переносит_заглушку_и_печатает_notice(test_db: str, client) -> None:
    """Строка «до миграции»: `drive_file_id` без ключа → `drive` с собранным адресом.

    Заглушка заводилась в 001 под файлы, поэтому адрес собирается по форме файла.
    Прогон идёт настоящим SQL миграции — тем же, что накатывается на прод, — и повторный
    прогон ничего не меняет (идемпотентность, AC-1).
    """
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 780008)
        conn.execute(
            "insert into file_object (participant_id, name, scope, uploaded_via, drive_file_id)"
            " values (%s,'Старая ссылка','personal','human_drive','legacyDrop01')", (pid,))
        conn.commit()
        assert not _check(conn, "F11"), "перенос пропущен — инвариант обязан покраснеть"

        sql = MIGRATION.read_text(encoding="utf-8")
        notices: list[str] = []

        def notice(diag) -> None:          # noqa: ANN001 — сигнатура psycopg
            notices.append(str(diag.message_primary))

        conn.add_notice_handler(notice)
        conn.execute(sql)
        conn.commit()
        row = conn.execute(
            "select storage, drive_url, object_key from file_object"
            " where drive_file_id = 'legacyDrop01'").fetchone()
        assert row[0] == "drive"
        assert row[1] == "https://drive.google.com/file/d/legacyDrop01/view"
        assert row[2] is None
        assert any("перенесённых в drive" in text for text in notices), notices
        assert _check(conn, "F11") and _check(conn, "F12")

        # Повторный накат: строк не переносит, ошибок не даёт.
        conn.execute(sql)
        conn.commit()
        assert _check(conn, "F11") and _check(conn, "F12") and _check(conn, "F13")

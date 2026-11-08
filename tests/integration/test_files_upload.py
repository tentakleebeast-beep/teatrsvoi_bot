"""Загрузка файла от бота: 20 МБ проходит, 25 МБ отбивается, чужое не создаётся (Ш-3).

Тест живой: кладёт настоящие байты в Garage и проверяет, что они там есть. Без поднятого
хранилища он пропускается — в CI Garage нет, и «зелёный» тест без проверки был бы ложью.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
from urllib.parse import quote

import psycopg
import pytest

from tests.conftest import _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]

try:                                    # настройки хранилища живут в .env проекта
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:                     # pragma: no cover — dotenv есть в requirements
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят: STORAGE_ENDPOINT не задан")

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ZIP = b"PK\x03\x04" + b"\x00" * 64


@pytest.fixture()
def store_cleanup():
    """Убрать за собой байты в Garage: тест не должен копить мусор в бакете."""
    keys: list[str] = []
    yield keys
    if not keys:
        return
    from app import files_store

    client = files_store.storage()
    for key in keys:
        try:
            client.delete_object(Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass


def _upload(client, auth: dict, name: str, data: bytes, mime: str):
    headers = {**auth, "X-File-Name": quote(name), "X-File-Mime": mime}
    return client.post("/files/upload", content=data, headers=headers)


def _row(file_id: int, dsn: str | None = None) -> dict:
    from psycopg.rows import dict_row

    with psycopg.connect(dsn or _test_dsn(), row_factory=dict_row) as conn:
        return conn.execute(
            "select f.id, f.name, f.kind, f.size_bytes, f.object_key, f.sha256, f.uploaded_via,"
            "       (select path from folder where id = ff.folder_id) as folder"
            "  from file_object f left join file_folder ff on ff.file_id = f.id where f.id = %s",
            (file_id,)).fetchone()


def test_документ_сохраняется_в_личную_папку(client, auth, store_cleanup) -> None:
    response = _upload(client, auth, "пьеса.pdf", PDF, "application/pdf")
    assert response.status_code == 200, response.text
    body = response.json()
    store_cleanup.append(_row(body["id"])["object_key"])

    assert body["ok"] is True
    assert body["kind"] == "text"
    assert body["folder"] == "Личное/Мои документы"
    assert body["duplicate"] is False
    assert body["size_bytes"] == len(PDF)
    # 2 ГБ минус файл: округление до мегабайта, поэтому проверяем диапазон, а не точное число
    assert 2030 <= body["quota_left_mb"] <= 2048

    row = _row(body["id"])
    assert row["name"] == "пьеса.pdf" and row["uploaded_via"] == "human_telegram"
    assert row["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert "а" not in row["object_key"].lower()          # в ключе нет кириллицы (F4)

    # байты действительно в хранилище, а не только в БД
    from app import files_store

    head = files_store.storage().head_object(
        Bucket=files_store.bucket_private(), Key=row["object_key"])
    assert head["ContentLength"] == len(PDF)


def test_картинка_идёт_в_папку_фото(client, auth, store_cleanup) -> None:
    response = _upload(client, auth, "костюм.png", PNG, "image/png")
    assert response.status_code == 200, response.text
    body = response.json()
    store_cleanup.append(_row(body["id"])["object_key"])
    assert body["kind"] == "image"
    assert body["folder"] == "Личное/Фото"


def test_повторная_загрузка_не_дублирует_байты(client, auth, store_cleanup) -> None:
    first = _upload(client, auth, "смета.pdf", PDF, "application/pdf").json()
    store_cleanup.append(_row(first["id"])["object_key"])
    second = _upload(client, auth, "смета (копия).pdf", PDF, "application/pdf").json()

    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    with psycopg.connect(_test_dsn()) as conn:
        count = conn.execute(
            "select count(*) from file_object where sha256 = %s",
            (hashlib.sha256(PDF).hexdigest(),)).fetchone()[0]
    assert count == 1


def test_архив_отбивается(client, auth) -> None:
    response = _upload(client, auth, "материалы.zip", ZIP, "application/zip")
    assert response.status_code == 415, response.text
    assert response.json()["code"] == "file_archive"


def test_пустой_файл_отбивается(client, auth) -> None:
    response = _upload(client, auth, "пусто.pdf", b"", "application/pdf")
    assert response.status_code == 400


def test_больше_лимита_отбивается_до_хранилища(client, auth, monkeypatch) -> None:
    """Предел Bot API: 20 МБ. Проверяем логику на маленьком пределе, а не 21 МБ в памяти."""
    from app import files_store

    monkeypatch.setattr(files_store, "MAX_UPLOAD_BYTES", 1024)
    response = _upload(client, auth, "большой.pdf", b"%PDF" + b"x" * 4000, "application/pdf")
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "file_too_large"
    assert "Mini App" in response.json()["detail"]


def test_квота_считается_и_отбивается(client, auth, monkeypatch, store_cleanup) -> None:
    from app import files_store

    response = _upload(client, auth, "первый.pdf", PDF, "application/pdf").json()
    store_cleanup.append(_row(response["id"])["object_key"])

    monkeypatch.setattr(files_store, "QUOTA_BYTES", len(PDF) + 10)
    second = _upload(client, auth, "второй.pdf", PDF + b"<!-- other -->", "application/pdf")
    assert second.status_code == 413, second.text
    assert second.json()["code"] == "file_quota_exceeded"

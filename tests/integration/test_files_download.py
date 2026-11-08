"""Выдача файла: свой — по кнопке, чужой — никогда (FR-K7, FR-K8).

Проверяется не «ручка отвечает», а два обещания: байты возвращаются ровно те, что положили,
и чужой файл неотличим от несуществующего — 404 в обоих случаях, без подсказки «есть, но не ваш».
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import urllib.request
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

PDF = b"%PDF-1.7\n" + b"x" * 200


@pytest.fixture()
def uploaded(client, auth):
    """Один загруженный файл плюс уборка байтов и записи после теста."""
    response = client.post("/files/upload", content=PDF,
                           headers={**auth, "X-File-Name": quote("пьеса.pdf"),
                                    "X-File-Mime": "application/pdf"})
    assert response.status_code == 200, response.text
    body = response.json()
    yield body
    from app import files_store

    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (body["id"],)).fetchone()[0]
    if key:
        try:
            files_store.storage().delete_object(Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass


def _other_person(chat_id: int) -> dict:
    """Второй действующий участник: проверяем, что он не получит чужое."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s, 'Второй Человек', 'второй-человек', 'troupe', 'active')"
            " on conflict (tg_chat_id) do nothing", (chat_id,))
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def test_список_показывает_свои_файлы(client, auth, uploaded) -> None:
    listed = client.get("/files", headers=auth).json()
    assert listed["ok"] is True
    assert any(row["id"] == uploaded["id"] for row in listed["data"])


def test_владелец_получает_ровно_свои_байты(client, auth, uploaded) -> None:
    meta = client.get(f"/files/{uploaded['id']}", headers=auth).json()
    assert meta["ok"] is True and meta["name"] == "пьеса.pdf"

    content = client.get(f"/files/{uploaded['id']}/content", headers=auth)
    assert content.status_code == 200
    assert content.content == PDF
    assert hashlib.sha256(content.content).hexdigest() == hashlib.sha256(PDF).hexdigest()


def test_чужие_метаданные_и_байты_не_отдаются(client, uploaded) -> None:
    other = _other_person(555001)
    assert client.get(f"/files/{uploaded['id']}", headers=other).status_code == 404
    assert client.get(f"/files/{uploaded['id']}/content", headers=other).status_code == 404
    assert client.post(f"/files/{uploaded['id']}/link", headers=other).status_code == 404

    # и чужого файла нет в его списке
    listed = client.get("/files", headers=other).json()
    assert all(row["id"] != uploaded["id"] for row in listed["data"])


def test_несуществующий_файл_тоже_404(client, auth) -> None:
    """Одинаковый ответ на «нет такого» и «не ваш» — чтобы не выдавать наличие файла."""
    assert client.get("/files/99999999", headers=auth).status_code == 404


def test_ссылка_работает_и_живёт_15_минут(client, auth, uploaded) -> None:
    link = client.post(f"/files/{uploaded['id']}/link", headers=auth).json()
    assert link["ok"] is True and link["expires_in"] == 900
    assert "X-Amz-Signature" in link["url"] or "Signature" in link["url"]

    with urllib.request.urlopen(link["url"], timeout=15) as response:
        assert response.status == 200
        assert response.read() == PDF
        # имя файла уходит отдельным заголовком, кириллица — по RFC 5987
        assert "filename" in (response.headers.get("Content-Disposition") or "")


def test_удалённый_файл_не_отдаётся(client, auth, uploaded) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set deleted_at = now() where id = %s",
                     (uploaded["id"],))
    assert client.get(f"/files/{uploaded['id']}", headers=auth).status_code == 404
    assert client.get(f"/files/{uploaded['id']}/content", headers=auth).status_code == 404


def test_выдача_и_ссылка_попадают_в_аудит(client, auth, uploaded) -> None:
    client.get(f"/files/{uploaded['id']}/content", headers=auth)
    client.post(f"/files/{uploaded['id']}/link", headers=auth)
    with psycopg.connect(_test_dsn()) as conn:
        actions = {row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(uploaded["id"]),))}
    assert {"file.download", "file.link"} <= actions

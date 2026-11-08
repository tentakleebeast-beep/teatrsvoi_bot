"""Доступ админа к чужому личному файлу: только по причине и только на срок (FR-K9, Ш-7).

Главное обещание — «по умолчанию не видит никто». Поэтому тесты начинаются с отказа:
админ без записи в `access_grant` получает тот же 404, что и любой посторонний, а после
запроса доступ появляется, владелец получает уведомление и может его отозвать.
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

PDF = b"%PDF-1.7\n" + "секрет".encode() + b"x" * 64


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
def owner_file(client):
    """Файл в личной папке участника плюс уборка байтов после теста."""
    owner = _person(557001, "troupe")
    response = client.post("/files/upload", content=PDF,
                           headers={**owner, "X-File-Name": quote("смета.pdf"),
                                    "X-File-Mime": "application/pdf"})
    assert response.status_code == 200, response.text
    body = response.json()
    yield owner, body
    from app import files_store

    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (body["id"],)).fetchone()[0]
    if key:
        try:
            files_store.storage().delete_object(Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass


def test_админ_без_запроса_не_видит_личный_файл(client, owner_file) -> None:
    _owner, saved = owner_file
    admin = _person(557002, "admin")
    assert client.get(f"/files/{saved['id']}", headers=admin).status_code == 404
    assert client.get(f"/files/{saved['id']}/content", headers=admin).status_code == 404


def test_запрос_без_причины_отбивается(client, owner_file) -> None:
    _owner, saved = owner_file
    admin = _person(557003, "admin")
    empty = client.post(f"/admin/files/{saved['id']}/access", json={"reason": "   "}, headers=admin)
    assert empty.status_code == 400
    short = client.post(f"/admin/files/{saved['id']}/access", json={"reason": "ок"}, headers=admin)
    assert short.status_code == 422


def test_не_админ_не_может_открыть_доступ(client, owner_file) -> None:
    _owner, saved = owner_file
    troupe = _person(557004, "troupe")
    response = client.post(f"/admin/files/{saved['id']}/access",
                           json={"reason": "просто посмотреть"}, headers=troupe)
    assert response.status_code == 403
    assert response.json()["code"] == "access_forbidden"


def test_доступ_появляется_после_запроса_и_владелец_уведомлён(client, owner_file) -> None:
    owner, saved = owner_file
    admin = _person(557005, "admin")

    granted = client.post(f"/admin/files/{saved['id']}/access",
                          json={"reason": "сверка сметы за октябрь", "ttl_hours": 24},
                          headers=admin)
    assert granted.status_code == 200, granted.text
    assert granted.json()["ttl_hours"] == 24

    meta = client.get(f"/files/{saved['id']}", headers=admin)
    assert meta.status_code == 200 and meta.json()["as_admin"] is True
    assert client.get(f"/files/{saved['id']}/content", headers=admin).content == PDF

    with psycopg.connect(_test_dsn()) as conn:
        note = conn.execute(
            "select payload from notification where kind = 'file_access'").fetchone()
        actions = {row[0] for row in conn.execute(
            "select action from audit_event where entity_id = %s", (str(saved["id"]),))}
        admin_id = conn.execute("select id from participant where tg_chat_id = 557005").fetchone()[0]
        notified = conn.execute(
            "select notified_at from access_grant where admin_id = %s", (admin_id,)).fetchone()

    assert note is not None
    assert "смета.pdf" in note[0]["body"] and "сверка сметы" in note[0]["body"]
    assert "file.access.grant" in actions and "file.download" in actions
    assert notified is not None and notified[0] is not None


def test_отзыв_закрывает_доступ_сразу(client, owner_file) -> None:
    owner, saved = owner_file
    admin = _person(557006, "admin")
    client.post(f"/admin/files/{saved['id']}/access",
                json={"reason": "проверка"}, headers=admin)
    assert client.get(f"/files/{saved['id']}", headers=admin).status_code == 200

    revoked = client.post(f"/files/{saved['id']}/access/revoke", headers=owner)
    assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
    assert client.get(f"/files/{saved['id']}", headers=admin).status_code == 404

    listed = client.get(f"/files/{saved['id']}/access", headers=owner).json()
    assert listed["count"] == 0


def test_истёкший_доступ_не_действует(client, owner_file) -> None:
    _owner, saved = owner_file
    admin = _person(557007, "admin")
    client.post(f"/admin/files/{saved['id']}/access", json={"reason": "вчера"}, headers=admin)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update access_grant set expires_at = now() - interval '1 hour'"
                     " where file_id = %s", (saved["id"],))
    assert client.get(f"/files/{saved['id']}", headers=admin).status_code == 404


def test_на_свой_файл_доступ_не_запрашивают(client) -> None:
    admin = _person(557008, "admin")
    uploaded = client.post("/files/upload", content=PDF,
                           headers={**admin, "X-File-Name": quote("мой.pdf"),
                                    "X-File-Mime": "application/pdf"}).json()
    response = client.post(f"/admin/files/{uploaded['id']}/access",
                           json={"reason": "сам себе"}, headers=admin)
    assert response.status_code == 400
    assert response.json()["code"] == "access_own_file"


def test_общий_файл_читают_все_без_гранта(client, owner_file) -> None:
    """Общий файл — не личный: грант не нужен, но и «админ-доступ» не при чём (FR-K10)."""
    _owner, saved = owner_file
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set scope = 'shared' where id = %s", (saved["id"],))
    stranger = _person(557009, "troupe")
    meta = client.get(f"/files/{saved['id']}", headers=stranger)
    assert meta.status_code == 200
    assert meta.json()["as_admin"] is False

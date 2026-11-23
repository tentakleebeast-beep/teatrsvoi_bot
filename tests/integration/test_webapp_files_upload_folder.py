"""Загрузка в выбранную папку: `folder_id` в `presign` и `commit` (docs/development/192 §6.1–6.2).

Сегодня загрузка не спрашивает папку и кладёт всё в `Личное/<вид>`. Теперь файл ложится
туда, куда человек сказал: связь `file_folder` с выбранной папкой вместо личной, `scope`
берётся у папки. Папка без прав — 403 `folder_forbidden`, несуществующая — 404
`folder_not_found`; без `folder_id` поведение прежнее (FR-K11).

Загрузка в общую папку ставит `scope='shared'` и не меняет `used_bytes` (V-125).

Обоснование: docs/development/189 §D5, AC-9, AC-10, AC-11.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import urllib.request

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import file_row, headers, participant

ROOT = pathlib.Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.7\n" + b"x" * 128

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:                     # pragma: no cover
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят: STORAGE_ENDPOINT не задан")

ADMIN_CHAT = 123456789


def _upload(client, head: dict, name: str, *, folder_id: int | None = None,
            description: str | None = None) -> dict:
    payload = {"name": name, "mime": "application/pdf", "size": len(PDF)}
    if folder_id is not None:
        payload["folder_id"] = folder_id
    presign = client.post("/webapp/files/presign", json=payload, headers=head)
    assert presign.status_code == 200, presign.text
    slot = presign.json()
    request = urllib.request.Request(slot["upload_url"], data=PDF, method="PUT",
                                     headers={"Content-Type": "application/pdf"})
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
    body = {"object_key": slot["object_key"], "name": name, "mime": "application/pdf",
            "sha256": hashlib.sha256(PDF).hexdigest()}
    if folder_id is not None:
        body["folder_id"] = folder_id
    if description is not None:
        body["description"] = description
    commit = client.post("/webapp/files/commit", json=body, headers=head)
    assert commit.status_code == 200, commit.text
    return {"presign": slot, "commit": commit.json()["file"]}


@pytest.fixture()
def folders(client):
    me = participant(ADMIN_CHAT)
    stranger = participant(770401, name="Чужой Владелец")
    with psycopg.connect(_test_dsn()) as conn:
        shared = conn.execute("select id from folder where scope = 'shared' and path = %s",
                              ("Документы",)).fetchone()[0]
        personal = conn.execute(
            "insert into folder (scope, participant_id, name, path)"
            " values ('personal', %s, 'Свои сметы', 'Свои сметы') returning id",
            (me,)).fetchone()[0]
        foreign = conn.execute(
            "insert into folder (scope, participant_id, name, path)"
            " values ('personal', %s, 'Чужое', 'Чужое') returning id",
            (stranger,)).fetchone()[0]
        conn.commit()
    return {"me": me, "shared": int(shared), "personal": int(personal),
            "foreign": int(foreign), "stranger": stranger}


def _used(me: int) -> int:
    from app import files_store

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return files_store.used_bytes(conn, me)


def test_файл_ложится_именно_в_выбранную_папку(client, folders) -> None:
    """`folder_id` в ответе и в `file_folder` — а не «Личное/Мои документы»."""
    head = headers(client)
    result = _upload(client, head, "Смета Пурги.pdf", folder_id=folders["personal"],
                     description="  смета октября ")
    file_id = result["commit"]["id"]
    assert result["commit"]["folder_id"] == folders["personal"]
    assert result["commit"]["folder"] == "Свои сметы"
    assert result["commit"]["description"] == "смета октября"
    assert result["commit"]["kind_word"] == "документ"
    assert result["commit"]["storage"] == "garage"

    with psycopg.connect(_test_dsn()) as conn:
        linked = [int(row[0]) for row in conn.execute(
            "select folder_id from file_folder where file_id = %s", (file_id,)).fetchall()]
    assert linked == [folders["personal"]], "файл попал не только в выбранную папку"
    assert file_row(file_id)["storage"] == "garage"


def test_без_folder_id_поведение_прежнее(client, folders) -> None:
    """Без папки файл идёт в `Личное/<вид>` — FR-K11 не изменился."""
    result = _upload(client, headers(client), "Заметки.pdf")
    assert result["commit"]["folder"] == "Личное/Мои документы"


def test_общая_папка_даёт_shared_и_не_тратит_личное_место(client, folders) -> None:
    """V-125: в общей папке файл занимает общий диск, а не личные 2 ГБ."""
    me = folders["me"]
    before = _used(me)
    result = _upload(client, headers(client), "Пурга.pdf", folder_id=folders["shared"])
    file_id = result["commit"]["id"]
    assert file_row(file_id)["scope"] == "shared"
    assert _used(me) == before, "файл в общей папке изменил личное место"
    assert result["presign"]["folder"]["scope"] == "shared"


def test_папка_без_прав_403_и_несуществующая_404(client, folders) -> None:
    """Отказы папки — словами и до загрузки байтов: presign проверяет папку первой."""
    head = headers(client, 770402)
    forbidden = client.post("/webapp/files/presign",
                            json={"name": "Смета.pdf", "mime": "application/pdf",
                                  "size": len(PDF), "folder_id": folders["foreign"]},
                            headers=head)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "folder_forbidden"

    missing = client.post("/webapp/files/presign",
                          json={"name": "Смета.pdf", "mime": "application/pdf",
                                "size": len(PDF), "folder_id": 999_999},
                          headers=head)
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "folder_not_found"

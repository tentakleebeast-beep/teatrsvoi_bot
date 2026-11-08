"""Порядок треков, папка как плейлист и архив (Ш-9, FR-M4…M6).

Главная ловушка, которую проверяем: «02» должно идти раньше «10». Обычная сортировка
по имени поставила бы «10 — финал» вторым, и музыка на показе поехала бы не туда.
"""

from __future__ import annotations

import io
import os
import pathlib
import zipfile
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

PDF = b"%PDF-1.7\n" + b"x" * 64


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


def _upload(client, headers: dict, name: str) -> dict:
    # содержимое делаем уникальным: одинаковые байты дедуплицируются, и файл был бы один
    body = PDF + name.encode()
    response = client.post("/files/upload", content=body,
                           headers={**headers, "X-File-Name": quote(name),
                                    "X-File-Mime": "application/pdf"})
    assert response.status_code == 200, response.text
    return response.json()


def _personal_folder(chat_id: int, kind: str = "Аудио") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "select f.id from folder f join participant p on p.id = f.participant_id"
            " where p.tg_chat_id = %s and f.path = %s", (chat_id, f"Личное/{kind}")).fetchone()
    assert row is not None, "папка не создана при загрузке"
    return int(row[0])


def _names(payload: dict) -> list[str]:
    return [item["name"] for item in payload["data"]]


def test_числовые_префиксы_сортируются_как_в_плеере(client, cleanup) -> None:
    owner = _person(558001)
    created = [_upload(client, owner, name) for name in
               ("10 — финал.pdf", "02 — выход Пьеро.pdf", "01 — пролог.pdf")]
    cleanup.extend(_object_key(item["id"]) for item in created)
    folder_id = _personal_folder(558001, "Мои документы")

    listed = client.get(f"/folders/{folder_id}/files", headers=owner).json()
    assert _names(listed) == ["01 — пролог.pdf", "02 — выход Пьеро.pdf", "10 — финал.pdf"]


def test_порядок_задаётся_вручную_и_перебивает_имена(client, cleanup) -> None:
    owner = _person(558002)
    created = [_upload(client, owner, name) for name in
               ("01 — первый.pdf", "02 — второй.pdf", "03 — третий.pdf")]
    cleanup.extend(_object_key(item["id"]) for item in created)
    folder_id = _personal_folder(558002, "Мои документы")
    ids = [item["id"] for item in created]

    reordered = client.post(f"/folders/{folder_id}/order",
                            json={"file_ids": [ids[2], ids[0], ids[1]]}, headers=owner)
    assert reordered.status_code == 200 and reordered.json()["updated"] == 3

    listed = client.get(f"/folders/{folder_id}/files", headers=owner).json()
    assert _names(listed) == ["03 — третий.pdf", "01 — первый.pdf", "02 — второй.pdf"]


def test_файл_переезжает_между_папками(client, cleanup) -> None:
    owner = _person(558003)
    saved = _upload(client, owner, "шум дождя.pdf")
    cleanup.append(_object_key(saved["id"]))
    source = _personal_folder(558003, "Мои документы")

    moved = client.post(f"/files/{saved['id']}/folder", json={"folder_id": None}, headers=owner)
    assert moved.status_code == 200
    assert _names(client.get(f"/folders/{source}/files", headers=owner).json()) == []

    back = client.post(f"/files/{saved['id']}/folder", json={"folder_id": source}, headers=owner)
    assert back.status_code == 200, back.text
    assert "шум дождя.pdf" in _names(client.get(f"/folders/{source}/files", headers=owner).json())


def test_архив_папки_идёт_в_порядке_экрана(client, cleanup) -> None:
    owner = _person(558004)
    created = [_upload(client, owner, name) for name in
               ("10 — финал.pdf", "02 — выход.pdf", "01 — пролог.pdf")]
    cleanup.extend(_object_key(item["id"]) for item in created)
    folder_id = _personal_folder(558004, "Мои документы")

    archive = client.get(f"/folders/{folder_id}/zip", headers=owner)
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        names = bundle.namelist()
    # префикс не дублируется: имена уже начинаются с цифр
    assert names == ["01 — пролог.pdf", "02 — выход.pdf", "10 — финал.pdf"]


def test_чужую_личную_папку_не_прочитать(client, cleanup) -> None:
    owner = _person(558005)
    saved = _upload(client, owner, "смета.pdf")
    cleanup.append(_object_key(saved["id"]))
    folder_id = _personal_folder(558005, "Мои документы")
    stranger = _person(558006)

    assert client.get(f"/folders/{folder_id}/files", headers=stranger).status_code == 404
    assert client.get(f"/folders/{folder_id}/zip", headers=stranger).status_code == 404


def test_в_мини_приложении_порядок_задаёт_админ_а_участник_получает_отказ(client, cleanup) -> None:
    """Порядок задаёт владелец папки, а роль без `files.access` получает отказ.

    С 08.11.2026 экран папки открыт правом `files.access` (V-039 (в): admin, org, master),
    поэтому отказ на `/webapp/folder/.../order` получает студиец — у него права нет
    (миграция 062), а не «любой, кроме админа», как было при `admin.panel`.
    """
    from app import core

    from tests.integration.test_webapp_profile import BOT_TOKEN, init_data

    core.BOT_TOKEN = BOT_TOKEN
    admin = _person(558007, "admin")
    saved = _upload(client, admin, "трек.pdf")
    cleanup.append(_object_key(saved["id"]))
    folder_id = _personal_folder(558007, "Мои документы")

    token = client.post("/webapp/auth",
                        json={"init_data": init_data(558007)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    payload = client.get(f"/webapp/folder/{folder_id}/files", headers=headers).json()
    assert payload["ok"] is True
    assert payload["data"] and payload["data"][0]["url"].startswith("http")

    own = client.post(f"/webapp/folder/{folder_id}/order",
                      json={"file_ids": [saved["id"]]}, headers=headers)
    assert own.status_code == 200, own.text

    _person(558008, "troupe")
    stranger_token = client.post("/webapp/auth",
                                 json={"init_data": init_data(558008)}).json()["token"]
    denied = client.post(f"/webapp/folder/{folder_id}/order", json={"file_ids": [saved["id"]]},
                         headers={"Authorization": f"Bearer {stranger_token}"})
    assert denied.status_code == 403


def _object_key(file_id: int) -> str:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select object_key from file_object where id = %s",
                           (file_id,)).fetchone()
    return row[0] if row else ""

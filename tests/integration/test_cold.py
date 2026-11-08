"""Холод: файл уезжает на второй том и возвращается при первом запросе (Ш-11, FR-K14).

Проверяется главное обещание: файл, к которому давно не обращались, не теряется —
он переезжает и возвращается прозрачно для человека. И обратное: горячий файл ротация
не трогает, даже если он старый, но нужный.
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

PDF = b"%PDF-1.7\n" + b"cold-test" * 20

# Служебные маршруты хранилища закрыты сервисным токеном (AUD-8, docs/reports/124).
SERVICE = {"Authorization": "Bearer test-token"}


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
def cold_volume(tmp_path, monkeypatch):
    from app import cold

    monkeypatch.setenv("STORAGE_COLD_DIR", str(tmp_path / "cold"))
    return cold.cold_dir()


@pytest.fixture()
def uploaded(client):
    owner = _person(560001)
    saved = client.post("/files/upload", content=PDF,
                        headers={**owner, "X-File-Name": quote("архив.pdf"),
                                 "X-File-Mime": "application/pdf"}).json()
    yield owner, saved
    from app import files_store

    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (saved["id"],)).fetchone()[0]
        cold_at = conn.execute("select cold_at from file_object where id = %s",
                               (saved["id"],)).fetchone()[0]
    try:
        if cold_at is None and key:
            files_store.storage().delete_object(
                Bucket=files_store.bucket_private(), Key=key)
    except Exception:                   # noqa: BLE001
        pass


def _object_key(file_id: int) -> str:
    with psycopg.connect(_test_dsn()) as conn:
        return conn.execute("select object_key from file_object where id = %s",
                            (file_id,)).fetchone()[0]


def test_старый_файл_уезжает_и_возвращается(client, uploaded, cold_volume) -> None:
    from app import cold, files_store

    owner, saved = uploaded
    key = _object_key(saved["id"])

    with psycopg.connect(_test_dsn()) as conn:
        result = cold.move_to_cold(conn, days=0)
        conn.commit()
    assert result["moved"] >= 1

    # в бакете объекта больше нет, на холодном томе — есть
    assert files_store.head_of(key) is None
    assert (cold_volume / key).is_file()
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select cold_at from file_object where id = %s",
                            (saved["id"],)).fetchone()[0] is not None

    # первый же запрос возвращает файл в горячий бакет и отдаёт те же байты
    content = client.get(f"/files/{saved['id']}/content", headers=owner)
    assert content.status_code == 200
    assert content.content == PDF
    assert files_store.head_of(key) is not None
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select cold_at from file_object where id = %s",
                            (saved["id"],)).fetchone()[0] is None
    assert not (cold_volume / key).exists()


def test_свежий_файл_ротация_не_трогает(client, uploaded, cold_volume) -> None:
    from app import cold, files_store

    _owner, saved = uploaded
    key = _object_key(saved["id"])
    with psycopg.connect(_test_dsn()) as conn:
        cold.move_to_cold(conn, days=180)
        conn.commit()
        cold_at = conn.execute("select cold_at from file_object where id = %s",
                               (saved["id"],)).fetchone()[0]
    assert cold_at is None, "файл загружен только что — в холод ему рано"
    assert files_store.head_of(key) is not None


def test_нет_холодной_копии_это_410(client, uploaded, cold_volume) -> None:
    """Если файла нет ни там, ни там — честная ошибка, а не пустой ответ."""
    from app import cold

    owner, saved = uploaded
    with psycopg.connect(_test_dsn()) as conn:
        cold.move_to_cold(conn, days=0)
        conn.commit()
        key = conn.execute("select object_key from file_object where id = %s",
                           (saved["id"],)).fetchone()[0]
    (cold_volume / key).unlink()

    response = client.get(f"/files/{saved['id']}/content", headers=owner)
    assert response.status_code == 410
    assert response.json()["code"] == "file_cold_missing"


def test_состояние_хранилища_считает_холод(client, uploaded, cold_volume) -> None:
    from app import cold

    _person(560002, "admin")
    with psycopg.connect(_test_dsn()) as conn:
        cold.move_to_cold(conn, days=0)
        conn.commit()
        data = cold.status(conn)
        space = cold.free_space()

    assert data["cold_files"] >= 1
    assert data["cold_bytes"] >= len(PDF)
    assert space["free"] > 0

    api_data = client.get("/admin/files/storage", headers=SERVICE).json()
    assert api_data["ok"] is True and "cold_files" in api_data


def test_хранилище_без_сервисного_токена_закрыто(client, uploaded, cold_volume) -> None:
    """AUD-8: снимок хранилища — служебный маршрут, сессия участника его не открывает.

    Раньше проверка ролей в теле из-за приоритета `&` пропускала любого участника
    (docs/architecture/25 §9.7); теперь вход — сервисный Bearer-токен.
    """
    admin = _person(560002, "admin")
    assert client.get("/admin/files/storage", headers=admin).status_code in (401, 403)
    assert client.post("/admin/files/cold-rotate", headers=admin).status_code in (401, 403)

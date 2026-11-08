"""Mini App: загрузка мимо Telegram — presign, PUT в хранилище, подтверждение (Ш-5).

Тест идёт тем же путём, что браузер: получает ссылку, кладёт байты напрямую в хранилище
и подтверждает загрузку. Поэтому он ловит и ошибки прав, и то, что ссылка вообще рабочая,
и то, что CORS включён — без него браузер файл не отправит.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import urllib.request

import psycopg
import pytest

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data

ROOT = pathlib.Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:                     # pragma: no cover
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят: STORAGE_ENDPOINT не задан")

PDF = b"%PDF-1.7\n" + b"x" * 128


def _headers(client) -> dict:
    # Подпись initData проверяется токеном бота: без него /webapp/auth отвечает 401,
    # и тест молча падал на `KeyError: 'token'` (он идёт только с поднятым Garage).
    from app import core

    core.BOT_TOKEN = BOT_TOKEN
    # Файлы открыты только администратору (П-7): фикстура даёт владельцу эту роль.
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, 'admin' from participant where tg_chat_id = %s"
            " on conflict do nothing", (ADMIN_CHAT,))
        conn.commit()
    token = client.post("/webapp/auth",
                        json={"init_data": init_data(ADMIN_CHAT)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def cleanup():
    keys: list[str] = []
    yield keys
    from app import files_store

    for key in keys:
        try:
            files_store.storage().delete_object(
                Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass


def _browser_upload(client, headers: dict, name: str, data: bytes, mime: str,
                    origin: str | None = None) -> dict:
    """То же, что делает страница: presign → PUT → commit."""
    presign = client.post("/webapp/files/presign",
                          json={"name": name, "mime": mime, "size": len(data)},
                          headers=headers).json()
    assert presign.get("ok"), presign
    request = urllib.request.Request(presign["upload_url"], data=data, method="PUT",
                                     headers={"Content-Type": mime})
    if origin:
        request.add_header("Origin", origin)
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        cors = response.headers.get("Access-Control-Allow-Origin")
    commit = client.post("/webapp/files/commit",
                         json={"object_key": presign["object_key"], "name": name,
                               "mime": mime, "sha256": hashlib.sha256(data).hexdigest()},
                         headers=headers).json()
    return {"presign": presign, "commit": commit, "cors": cors}


def test_страница_файлов_открывается(client) -> None:
    page = client.get("/webapp/files")
    assert page.status_code == 200
    assert "Файлы" in page.text and "/webapp/files/presign" in page.text


def test_браузерная_загрузка_доходит_до_списка(client, cleanup) -> None:
    headers = _headers(client)
    result = _browser_upload(client, headers, "пьеса.pdf", PDF, "application/pdf")
    assert result["commit"].get("ok"), result["commit"]
    saved = result["commit"]["file"]
    cleanup.append(result["presign"]["object_key"])

    assert saved["folder"] == "Личное/Мои документы"
    assert saved["duplicate"] is False

    listed = client.get("/webapp/files/list", headers=headers).json()
    assert listed["ok"] is True
    assert any(row["id"] == saved["id"] for row in listed["data"])
    assert listed["quota"]["total"] == 2 * 1024 ** 3
    assert listed["quota"]["left"] < listed["quota"]["total"]


def test_cors_разрешён_для_браузера(client, cleanup) -> None:
    """Без этого заголовка браузер не покажет ответ на прямой PUT в хранилище."""
    headers = _headers(client)
    result = _browser_upload(client, headers, "афиша.pdf", PDF + b"1", "application/pdf",
                             origin="http://127.0.0.1:8000")
    cleanup.append(result["presign"]["object_key"])
    allowed = result["cors"] or ""
    # ровно один origin: список через запятую браузер отвергает целиком
    assert allowed in ("*", "http://127.0.0.1:8000"), allowed
    assert "," not in allowed


def test_чужой_ключ_не_подтвердить(client) -> None:
    headers = _headers(client)
    response = client.post("/webapp/files/commit",
                           json={"object_key": "p999999/чужой.pdf", "name": "чужой.pdf",
                                 "mime": "application/pdf"},
                           headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "file_key_foreign"


def test_незагруженный_файл_не_подтверждается(client) -> None:
    headers = _headers(client)
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()[0]
    response = client.post("/webapp/files/commit",
                           json={"object_key": f"p{pid}/нет-такого.pdf",
                                 "name": "нет.pdf", "mime": "application/pdf"},
                           headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "file_not_uploaded"


def test_видео_не_получает_ссылку(client) -> None:
    headers = _headers(client)
    response = client.post("/webapp/files/presign",
                           json={"name": "фильм.mp4", "mime": "video/mp4", "size": 1000},
                           headers=headers)
    assert response.status_code == 415
    assert response.json()["code"] == "file_video"


def test_превышение_потолка_отбивается_до_загрузки(client, monkeypatch) -> None:
    from app import files_store

    headers = _headers(client)
    monkeypatch.setattr(files_store, "MAX_MINIAPP_BYTES", 1000)
    response = client.post("/webapp/files/presign",
                           json={"name": "большой.pdf", "mime": "application/pdf",
                                 "size": 5000},
                           headers=headers)
    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"

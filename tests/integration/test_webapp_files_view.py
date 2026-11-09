"""`GET /webapp/files/{id}/view`: ссылка на просмотр, права и аудит (А3, К4–К7).

Основание — спецификация А2 §3.1 и дизайн `docs/development/150-miniapp-file-view.md`.
До карты А3 маршрута «ссылка на один файл для Mini App» не было: экран папки выдавал
ссылки только на папку, а `POST /files/{id}/link` живёт в контуре чата.

Здесь проверяется поведение на живой схеме, без браузера:

* 200 для своего файла: весь набор полей §3.1, `expires_in=900`, `url` подписан
  как `inline` (а не `attachment`), ключ объекта наружу не отдаётся;
* видимость: чужой личный — 404 (как несуществующий), файл общей папки — 200,
  админ с активным грантом — 200 и `as_admin: true`;
* `troupe` без `files.access` — 403 и запись `access.denied` в аудите;
* холодный файл восстанавливается до выдачи ссылки; нет холодной копии — 410;
* каждый 200 пишет `file.view` с `via: "webapp"`.

Хранилище для проверок не нужно: подпись считает подменённый `public_client`,
который запоминает параметры. Настоящий `Content-Disposition: inline` проверяется
отдельным тестом — он идёт только там, где поднят Garage (`STORAGE_ENDPOINT`).

Обоснование: FR-K8, FR-K9, FR-K10, `docs/development/86-files-and-storage.md`,
`docs/development/150-miniapp-file-view.md` §3, карта t_a97e9a0c.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.request
import uuid

import psycopg
import pytest

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_files_actions_screen import (
    _person,
    _webapp_headers,
)
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _participant_id(chat_id: int) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute("select id from participant where tg_chat_id = %s",
                                (chat_id,)).fetchone()[0])


def _file(chat_id: int, name: str = "смета.pdf", *, kind: str = "text",
          mime: str = "application/pdf", key: str | None = None) -> tuple[int, str]:
    """Строка файла в хранилище человека. Байты для проверок прав не нужны."""
    participant_id = _participant_id(chat_id)
    object_key = key if key is not None else f"p{participant_id}/view-{uuid.uuid4().hex}.pdf"
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key, bucket, kind)"
            " values (%s,%s,%s,2048,'personal','webapp',%s,'svoi-private',%s) returning id",
            (participant_id, name, mime, object_key, kind)).fetchone()
        conn.commit()
        return int(row[0]), object_key


def _chat_headers(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _audit(file_id: int, action: str | None = None) -> list[dict]:
    """События аудита по файлу: последние сверху."""
    query = ("select action, actor_id, after from audit_event"
             " where entity_type = 'file_object' and entity_id = %s")
    params: tuple = (str(file_id),)
    if action:
        query += " and action = %s"
        params += (action,)
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(query + " order by id desc", params).fetchall()
    return [{"action": row[0], "actor_id": row[1],
             "after": row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")}
            for row in rows]


class _FakeSigner:
    """Подписант без хранилища: запоминает, что и на сколько попросили подписать."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:
        self.calls.append({"operation": operation, "params": Params, "ttl": ExpiresIn})
        return f"https://files.example.test/{Params['Key']}?X-Amz-Signature=test"


@pytest.fixture()
def signer(monkeypatch) -> _FakeSigner:
    from app import files_store

    fake = _FakeSigner()
    monkeypatch.setattr(files_store, "public_client", lambda: fake)
    return fake


# --- 200: поля контракта и подпись -----------------------------------------


def test_свой_файл_отдаёт_все_поля_и_подпись_inline(client, signer) -> None:
    _person(571001, "master", "Мастер")
    file_id, object_key = _file(571001, "Гаммы для хора.PDF")
    view = client.get(f"/webapp/files/{file_id}/view",
                      headers=_webapp_headers(client, 571001))

    assert view.status_code == 200, view.text
    body = view.json()
    assert body["ok"] is True and body["id"] == file_id
    assert body["name"] == "Гаммы для хора.PDF"
    assert body["kind"] == "text" and body["mime"] == "application/pdf"
    assert body["size_bytes"] == 2048
    assert body["ext"] == "pdf", "расширение без точки и нижним регистром"
    assert body["expires_in"] == 900
    assert body["folder"] is None and body["as_admin"] is False
    assert body["cold_restored"] is False
    assert "object_key" not in view.text, "в ответе есть поле ключа объекта"

    assert len(signer.calls) == 1, "подпись не запрошена"
    call = signer.calls[0]
    assert call["operation"] == "get_object"
    assert call["ttl"] == 900
    assert call["params"]["ResponseContentDisposition"] == "inline", \
        "ссылка скачивает файл вместо просмотра"
    assert call["params"]["Key"] == object_key


def test_папка_файла_видна_в_ответе(client, signer) -> None:
    owner = _person(571002, "org", "Организатор")
    file_id, _key = _file(571002, "афиша.png", kind="image", mime="image/png")
    folder = client.post("/folders", json={"name": "Афиши", "scope": "shared"},
                         headers=owner).json()
    moved = client.post(f"/files/{file_id}/folder", json={"folder_id": folder["id"]},
                        headers=owner)
    assert moved.status_code == 200, moved.text

    view = client.get(f"/webapp/files/{file_id}/view",
                      headers=_webapp_headers(client, 571002)).json()
    assert view["folder"] == folder["path"]


# --- права ------------------------------------------------------------------


def test_чужой_личный_файл_не_отличим_от_несуществующего(client, signer) -> None:
    _person(571003, "org", "Владелец")
    _person(571004, "master", "Мастер")
    file_id, _key = _file(571003)

    foreign = client.get(f"/webapp/files/{file_id}/view",
                         headers=_webapp_headers(client, 571004))
    missing = client.get("/webapp/files/99999999/view",
                         headers=_webapp_headers(client, 571004))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "file_not_found"
    assert foreign.json()["detail"] == "Файл не найден."


def test_файл_общей_папки_видят_все_с_правом_раздела(client, signer) -> None:
    """FR-K10: истина — `folder.scope='shared'`, а не мёртвая колонка `file_object.scope`."""
    owner = _person(571005, "org", "Организатор")
    _person(571006, "master", "Мастер")
    file_id, _key = _file(571005, "программа.pdf")
    folder = client.post("/folders", json={"name": "Документы театра", "scope": "shared"},
                         headers=owner).json()
    client.post(f"/files/{file_id}/folder", json={"folder_id": folder["id"]}, headers=owner)

    view = client.get(f"/webapp/files/{file_id}/view",
                      headers=_webapp_headers(client, 571006))
    assert view.status_code == 200, view.text
    assert view.json()["as_admin"] is False


def test_админ_видит_чужой_файл_только_по_гранту(client, signer) -> None:
    _person(571007, "org", "Владелец")
    _person(571008, "admin", "Администратор")
    file_id, _key = _file(571007, "смета.pdf")

    closed = client.get(f"/webapp/files/{file_id}/view",
                        headers=_webapp_headers(client, 571008))
    assert closed.status_code == 404, "админ без гранта видит чужой личный файл"

    assert client.post(f"/admin/files/{file_id}/access",
                       json={"reason": "сверка сметы"},
                       headers=_chat_headers(571008)).status_code == 200
    opened = client.get(f"/webapp/files/{file_id}/view",
                        headers=_webapp_headers(client, 571008))
    assert opened.status_code == 200, opened.text
    assert opened.json()["as_admin"] is True


def test_роль_без_права_получает_403_и_след_в_аудите(client, signer) -> None:
    _person(571009, "troupe", "Студиец")
    file_id, _key = _file(571009, "моё.pdf", key="p571009/моё.pdf")
    response = client.get(f"/webapp/files/{file_id}/view",
                          headers=_webapp_headers(client, 571009))

    assert response.status_code == 403
    assert "capability required: files.access" in response.text
    with psycopg.connect(_test_dsn()) as conn:
        denied = conn.execute(
            "select count(*) from audit_event where action = 'access.denied'"
            "   and after::text like '%files.access%'").fetchone()[0]
    assert denied >= 1, "отказ по праву не попал в аудит"


# --- холод и незавершённая загрузка ----------------------------------------


def test_холодный_файл_возвращается_до_выдачи_ссылки(client, signer, monkeypatch,
                                                      tmp_path) -> None:
    from app import cold, files_store

    _person(571010, "org", "Владелец")
    file_id, object_key = _file(571010, "архив.pdf")
    cold_dir = tmp_path / "cold"
    monkeypatch.setenv("STORAGE_COLD_DIR", str(cold_dir))
    target = cold_dir / object_key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.7")

    put: list[str] = []
    monkeypatch.setattr(files_store, "storage", lambda: type(
        "Store", (), {"put_object": lambda self, **kwargs: put.append(kwargs["Key"])})())
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set cold_at = now() where id = %s", (file_id,))
        conn.commit()

    view = client.get(f"/webapp/files/{file_id}/view",
                      headers=_webapp_headers(client, 571010))
    assert view.status_code == 200, view.text
    assert view.json()["cold_restored"] is True
    assert put == [object_key], "файл не вернулся в горячий бакет"

    with psycopg.connect(_test_dsn()) as conn:
        cold_at = conn.execute("select cold_at from file_object where id = %s",
                               (file_id,)).fetchone()[0]
    assert cold_at is None, "метка холода не снята"
    assert cold.cold_dir() == cold_dir


def test_нет_холодной_копии_это_410(client, signer, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STORAGE_COLD_DIR", str(tmp_path / "empty-cold"))
    _person(571011, "org", "Владелец")
    file_id, _key = _file(571011, "потеря.pdf")
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set cold_at = now() where id = %s", (file_id,))
        conn.commit()

    response = client.get(f"/webapp/files/{file_id}/view",
                          headers=_webapp_headers(client, 571011))
    assert response.status_code == 410
    assert response.json()["code"] == "file_cold_missing"
    assert response.json()["detail"] == "Файл не найден ни в хранилище, ни в архиве."


def test_файл_без_ключа_объекта_просит_загрузить_заново(client, signer) -> None:
    _person(571012, "org", "Владелец")
    file_id, _key = _file(571012, "недолетел.pdf")
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set object_key = null where id = %s", (file_id,))
        conn.commit()

    response = client.get(f"/webapp/files/{file_id}/view",
                          headers=_webapp_headers(client, 571012))
    assert response.status_code == 404
    assert response.json()["code"] == "file_not_uploaded"
    assert response.json()["detail"] == "Файл не долетел до хранилища — загрузите заново."


# --- аудит ------------------------------------------------------------------


def test_каждый_просмотр_пишется_в_аудит(client, signer) -> None:
    _person(571013, "org", "Организатор")
    file_id, _key = _file(571013, "программа.pdf")

    assert client.get(f"/webapp/files/{file_id}/view",
                      headers=_webapp_headers(client, 571013)).status_code == 200
    events = _audit(file_id, "file.view")
    assert len(events) == 1, events
    assert events[0]["after"] == {"name": "программа.pdf", "kind": "text",
                                  "via": "webapp", "as_admin": False}


# --- настоящая подпись: только там, где поднят Garage -----------------------


@pytest.mark.skipif(not os.getenv("STORAGE_ENDPOINT"),
                    reason="Garage не поднят: STORAGE_ENDPOINT не задан")
def test_ссылка_реально_открывает_файл_а_не_скачивает(client) -> None:
    """К4: GET по выданной ссылке отвечает 200 и `Content-Disposition: inline`."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:                 # pragma: no cover
        pass

    from app import core

    core.BOT_TOKEN = BOT_TOKEN
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, 'admin' from participant where tg_chat_id = %s"
            " on conflict do nothing", (ADMIN_CHAT,))
        conn.commit()
    token = client.post("/webapp/auth",
                        json={"init_data": init_data(ADMIN_CHAT)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    pdf = b"%PDF-1.7\n" + b"x" * 64
    saved = client.post("/files/upload", content=pdf,
                        headers={**_chat_headers(ADMIN_CHAT), "Content-Type": "application/pdf",
                                 "X-File-Name": "inline.pdf",
                                 "X-File-Mime": "application/pdf"})
    assert saved.status_code == 200, saved.text
    file_id = saved.json()["id"]
    body = client.get(f"/webapp/files/{file_id}/view", headers=headers).json()
    try:
        with urllib.request.urlopen(body["url"], timeout=20) as response:
            assert response.status == 200
            disposition = response.headers.get("Content-Disposition") or ""
            assert disposition.startswith("inline"), disposition
    finally:
        from app import files_store

        with psycopg.connect(_test_dsn()) as conn:
            key = conn.execute("select object_key from file_object where id = %s",
                               (file_id,)).fetchone()[0]
        try:
            files_store.storage().delete_object(
                Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass

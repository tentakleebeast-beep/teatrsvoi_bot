"""Заметки: личные и общие, чтение своим и чужим, правка (FR-F7, docs/development/19).

Обоснование: FR-F7 — заметки нужны (П-9), дом — Mini App «Ещё → Заметки», в чате —
подтверждение разобранной заметки. Здесь проверяется серверная часть, общая для
обоих входов: видимость, права и запись.

Критерии приёмки карты t_700e8b2f:

* заметка создаётся и читается своим и общим;
* чужая личная заметка не видна и не правится.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import ADMIN_CHAT, TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _headers(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _webapp_headers(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_своя_личная_заметка_создаётся_и_читается(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810001, "Заметки Своя")

    created = client.post("/tools/notes", headers=_headers(810001), json={
        "title": "Взять реквизит", "body": "Взять реквизит из склада",
        "tags": ["реквизит", "#зал"], "scope": "personal"}).json()
    assert created["ok"] is True, created
    note = created["note"]
    assert note["scope"] == "personal" and note["mine"] is True
    assert note["tags"] == ["реквизит", "зал"]
    assert note["title"] == "Взять реквизит"

    listed = client.get("/tools/notes", headers=_headers(810001)).json()
    assert listed["ok"] is True
    assert [row["id"] for row in listed["notes"]] == [note["id"]]


def test_чужая_личная_заметка_не_видна_и_не_правится(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810002, "Заметки Автор")
        _person(conn, 810003, "Заметки Чужой")

    note = client.post("/tools/notes", headers=_headers(810002), json={
        "body": "личное, никому", "scope": "personal"}).json()["note"]
    assert note["title"] == "личное, никому", "заголовок не взялся из первой строки"

    listed = client.get("/tools/notes", headers=_headers(810003)).json()
    assert note["id"] not in [row["id"] for row in listed["notes"]]

    update = client.post(f"/tools/notes/{note['id']}", headers=_headers(810003), json={
        "body": "подменил чужое"})
    assert update.status_code == 404, update.text
    assert update.json()["code"] == "note_not_found"


def test_общая_заметка_видна_всем_и_правится_автором(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810004, "Заметки Общая Автор")
        _person(conn, 810005, "Заметки Общая Читатель")

    note = client.post("/tools/notes", headers=_headers(810004), json={
        "title": "Зал", "body": "В зале переставили станки", "scope": "shared"}).json()["note"]
    assert note["scope"] == "shared"

    seen = client.get("/tools/notes", headers=_headers(810005)).json()["notes"]
    row = next(item for item in seen if item["id"] == note["id"])
    assert row["mine"] is False and row["editable"] is False, row
    assert row["author"] == "Заметки Общая Автор"

    denied = client.post(f"/tools/notes/{note['id']}", headers=_headers(810005), json={
        "body": "чужое испортил"})
    assert denied.status_code == 404, denied.text

    fixed = client.post(f"/tools/notes/{note['id']}", headers=_headers(810004), json={
        "title": "Зал", "body": "В зале переставили станки и кулисы"}).json()
    assert fixed["ok"] is True and "кулисы" in fixed["note"]["body"]


def test_админ_правит_общую_но_не_открывает_чужую_личную(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810006, "Заметки Автор Заметки")
        _person(conn, 810009, "Заметки Администратор", role="admin")

    shared = client.post("/tools/notes", headers=_headers(810006), json={
        "body": "общая для всех", "scope": "shared"}).json()["note"]
    personal = client.post("/tools/notes", headers=_headers(810006), json={
        "body": "только моё", "scope": "personal"}).json()["note"]

    assert client.post(f"/tools/notes/{shared['id']}", headers=_headers(810009),
                       json={"body": "поправил админ"}).status_code == 200
    assert client.post(f"/tools/notes/{personal['id']}", headers=_headers(810009),
                       json={"body": "админ трогает личное"}).status_code == 404
    # и всё равно личная заметка не приезжает админу в список
    admin_list = client.get("/tools/notes", headers=_headers(810009)).json()["notes"]
    assert personal["id"] not in [row["id"] for row in admin_list]


def test_пустая_заметка_отклоняется(client) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810007, "Заметки Пустая")

    response = client.post("/tools/notes", headers=_headers(810007), json={
        "title": "", "body": "   ", "scope": "personal"})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "note_empty"


def test_экран_mini_app_читает_и_создаёт_ту_же_заметку(client) -> None:
    """Экран и чат — один путь записи: заметка с экрана видна маршруту чата."""
    _api()      # подпись initData проверяется токеном бота, как в test_webapp_profile
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 810008, "Заметки Экран")

    headers = _webapp_headers(client, 810008)
    saved = client.post("/webapp/notes/save", headers=headers, json={
        "title": "С экрана", "body": "записано с экрана", "scope": "shared"}).json()
    assert saved["ok"] is True, saved

    listed = client.get("/webapp/notes/list", headers=headers).json()
    assert saved["note"]["id"] in [row["id"] for row in listed["notes"]]

    chat_view = client.get("/tools/notes", headers=_headers(810008)).json()["notes"]
    assert saved["note"]["id"] in [row["id"] for row in chat_view]

    edited = client.post("/webapp/notes/save", headers=headers, json={
        "id": saved["note"]["id"], "title": "С экрана", "body": "исправлено",
        "scope": "shared"}).json()
    assert edited["note"]["body"] == "исправлено"

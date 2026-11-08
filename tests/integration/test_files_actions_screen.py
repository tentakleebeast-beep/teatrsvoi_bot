"""Файлы: действия на экране и в чате идут одним обработчиком и по одному праву (Ш-08).

До этого шага у дома файлов было два лица: экран закрыт `admin.panel`, а чат открыт
всем (`docs/reports/122` §4.3), доступ к файлу и утверждение тега жили только в чате,
а `rename`, `delete` и `order` не звал никто — маршруты были сиротами (`docs/reports/124`
§3, Ф1–Ф4). Здесь проверяется обратное:

* доступ к личному файлу выдаётся и отзывается одинаково — с экрана и из чата:
  тот же обработчик, тот же срок, тот же владелец в уведомлении;
* утверждение тега доступно администратору и недоступно участнику с любого входа;
* порядок папки сохраняется и читается одинаково с обоих входов;
* папку можно создать, переименовать и закрыть с экрана — тем же кодом, что в чате.

Тесты живые: работают на настоящем Postgres, строки файлов кладут в БД напрямую —
поэтому они не зависят от поднятого Garage и не пропускаются в CI.

Обоснование: FR-F5, FR-K9, FR-M8, П-7, `docs/development/86-files-and-storage.md`,
`docs/reports/124-actions-homes-and-requirements.md` §3 (Ф1–Ф5).
"""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data


def _signing() -> None:
    """Подпись initData проверяется токеном бота: в тестах он свой (как в анкете Mini App)."""
    from app import core

    core.BOT_TOKEN = BOT_TOKEN


def _person(chat_id: int, role: str, name: str | None = None) -> dict:
    """Участник с ролью: права считаются по `participant_role`, а не по колонке role."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') on conflict (tg_chat_id) do nothing",
            (chat_id, name or f"Человек {chat_id}", f"человек-{chat_id}", role))
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, %s from participant where tg_chat_id = %s on conflict do nothing",
            (role, chat_id))
        conn.commit()
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _webapp_headers(client, chat_id: int) -> dict:
    """Сессия Mini App: тот же вход, что у браузера, — токен из initData."""
    _signing()
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _participant_id(chat_id: int) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute("select id from participant where tg_chat_id = %s",
                                (chat_id,)).fetchone()[0])


def _file(chat_id: int, name: str = "смета.pdf") -> int:
    """Строка файла в личном хранилище человека. Байты для этих проверок не нужны."""
    participant_id = _participant_id(chat_id)
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key, bucket, kind)"
            " values (%s,%s,'application/pdf',1024,'personal','human_telegram',%s,"
            "         'svoi-private','text') returning id",
            (participant_id, name, f"p{participant_id}/sh08-{name}")).fetchone()
        conn.commit()
        return int(row[0])


def _grant(file_id: int) -> tuple | None:
    """Что именно записано в `access_grant`: без времени — оно у двух выдач разное."""
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "select admin_id, reason, (expires_at > now()) as alive"
            "  from access_grant where file_id = %s", (file_id,)).fetchone()
    return None if row is None else (int(row[0]), row[1], bool(row[2]))


def _tag_status(tag_id: int) -> str:
    with psycopg.connect(_test_dsn()) as conn:
        return str(conn.execute("select status from tag where id = %s", (tag_id,)).fetchone()[0])


def _names(client, folder_id: int, headers: dict) -> list[str]:
    payload = client.get(f"/folders/{folder_id}/files", headers=headers).json()
    return [row["name"] for row in payload["data"]]


# --- Ф2: доступ к файлу ------------------------------------------------------

def test_доступ_выдаётся_и_отзывается_одинаково_с_экрана_и_из_чата(client) -> None:
    _person(558001, "troupe", "Владелец файла")
    admin = _person(558002, "admin", "Администратор")
    screen = _webapp_headers(client, 558002)
    file_id = _file(558001)

    chat = client.post(f"/admin/files/{file_id}/access",
                       json={"reason": "сверка сметы", "ttl_hours": 5}, headers=admin)
    assert chat.status_code == 200, chat.text
    from_chat = _grant(file_id)
    assert from_chat is not None and from_chat[2] is True
    assert client.post(f"/files/{file_id}/access/revoke", headers=admin).status_code == 200
    assert _grant(file_id) is None

    from_screen = client.post(f"/webapp/files/{file_id}/access",
                              json={"reason": "сверка сметы", "ttl_hours": 5}, headers=screen)
    assert from_screen.status_code == 200, from_screen.text
    assert from_screen.json()["ttl_hours"] == chat.json()["ttl_hours"] == 5
    assert from_screen.json()["owner_id"] == chat.json()["owner_id"] == _participant_id(558001)
    assert _grant(file_id) == from_chat, "экран и чат записали разный доступ"

    listed = client.get(f"/webapp/files/{file_id}/access", headers=screen)
    assert listed.status_code == 200 and listed.json()["count"] == 1

    assert client.post(f"/webapp/files/{file_id}/access/revoke",
                       headers=screen).status_code == 200
    assert _grant(file_id) is None, "отзыв с экрана не закрыл доступ"


def test_участник_не_открывает_и_не_отзывает_доступ_ни_с_экрана_ни_из_чата(client) -> None:
    _person(558011, "troupe", "Владелец файла")
    troupe = _person(558012, "troupe", "Посторонний")
    file_id = _file(558011)

    assert client.post(f"/admin/files/{file_id}/access",
                       json={"reason": "просто посмотреть"}, headers=troupe).status_code == 403
    assert client.post(f"/webapp/files/{file_id}/access",
                       json={"reason": "просто посмотреть"},
                       headers=_webapp_headers(client, 558012)).status_code == 403
    assert _grant(file_id) is None


# --- Ф3: тег -----------------------------------------------------------------

def test_тег_утверждает_админ_и_не_утверждает_участник(client) -> None:
    author = _person(558021, "troupe", "Автор тега")
    draft = client.post("/tags", json={"title": "Реквизит Пурги"}, headers=author).json()
    assert draft["status"] == "draft", draft

    denied = client.post(f"/webapp/tags/{draft['id']}/approve",
                         headers=_webapp_headers(client, 558021))
    assert denied.status_code == 403
    assert _tag_status(draft["id"]) == "draft", "участник утвердил тег через экран"

    _person(558022, "admin", "Администратор")
    approved = client.post(f"/webapp/tags/{draft['id']}/approve",
                           headers=_webapp_headers(client, 558022))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert _tag_status(draft["id"]) == "approved"

    pending = client.get("/webapp/tags/pending", headers=_webapp_headers(client, 558022))
    assert pending.status_code == 200
    assert all(row["id"] != draft["id"] for row in pending.json()["data"])


def test_черновик_тега_виден_на_экране_до_утверждения(client) -> None:
    author = _person(558031, "troupe", "Автор тега")
    draft = client.post("/tags", json={"title": "Костюм Пурги"}, headers=author).json()
    _person(558032, "admin", "Администратор")

    pending = client.get("/webapp/tags/pending", headers=_webapp_headers(client, 558032)).json()
    assert [row["title"] for row in pending["data"]] == ["Костюм Пурги"]
    assert pending["data"][0]["id"] == draft["id"]


# --- Ф4: папки ---------------------------------------------------------------

def test_порядок_папок_сохраняется_и_одинаков_на_экране_и_в_чате(client) -> None:
    admin = _person(558041, "admin", "Администратор")
    screen = _webapp_headers(client, 558041)
    folder = client.post("/folders", json={"name": "Порядок Ш-08", "scope": "shared"},
                         headers=admin).json()
    files = [_file(558041, name) for name in
             ("10 — финал.pdf", "02 — выход.pdf", "01 — пролог.pdf")]
    for file_id in files:
        moved = client.post(f"/files/{file_id}/folder", json={"folder_id": folder["id"]},
                            headers=admin)
        assert moved.status_code == 200, moved.text

    # «02» раньше «10»: обычная сортировка по имени тут ошибается
    wanted = [files[2], files[1], files[0]]
    saved = client.post(f"/folders/{folder['id']}/order", json={"file_ids": wanted},
                        headers=admin)
    assert saved.status_code == 200, saved.text
    assert _names(client, folder["id"], admin) == ["01 — пролог.pdf", "02 — выход.pdf",
                                                   "10 — финал.pdf"]

    flipped = list(reversed(wanted))
    assert client.post(f"/webapp/folder/{folder['id']}/order", json={"file_ids": flipped},
                       headers=screen).status_code == 200
    assert _names(client, folder["id"], admin) == ["10 — финал.pdf", "02 — выход.pdf",
                                                   "01 — пролог.pdf"], \
        "порядок, сохранённый с экрана, не виден в чате"


def test_папку_создают_переименовывают_и_закрывают_с_экрана(client) -> None:
    _person(558051, "admin", "Администратор")
    screen = _webapp_headers(client, 558051)

    made = client.post("/webapp/folders", json={"name": "Реквизит Ш-08", "scope": "shared"},
                       headers=screen)
    assert made.status_code == 200, made.text
    folder_id = made.json()["id"]
    assert made.json()["path"] == "Реквизит Ш-08"

    renamed = client.post(f"/webapp/folders/{folder_id}/rename",
                          json={"name": "Реквизит и костюм"}, headers=screen)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["path"] == "Реквизит и костюм"
    paths = {row["path"] for row in client.get("/webapp/folders", headers=screen).json()["data"]}
    assert "Реквизит и костюм" in paths and "Реквизит Ш-08" not in paths

    closed = client.post(f"/webapp/folders/{folder_id}/delete", headers=screen)
    assert closed.status_code == 200, closed.text
    assert "Реквизит и костюм" not in {
        row["path"] for row in client.get("/webapp/folders", headers=screen).json()["data"]}


def test_участник_не_правит_папки_с_экрана(client) -> None:
    troupe = _person(558061, "troupe", "Участник")
    screen = _webapp_headers(client, 558061)
    assert client.post("/webapp/folders", json={"name": "Чужая папка", "scope": "shared"},
                       headers=screen).status_code == 403
    assert client.get("/webapp/folders", headers=screen).status_code == 403
    assert client.post("/webapp/folders/1/rename", json={"name": "Захват"},
                       headers=screen).status_code == 403
    assert troupe  # вход без прав — не ошибка входа, а отказ по праву

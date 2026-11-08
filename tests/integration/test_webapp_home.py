"""Хаб отдаёт плитки и счётчики.

Дефект проды 30.10.2026: `/webapp/home/data` падал с KeyError: 0 — соединение
отдаёт словари, а счётчики читались по индексу. Человек видел «не удалось
загрузить меню» вместо хаба.

Строки «Личного» на «Ещё» — docs/development/92 §9.1: строка файлов едет в ответе
данных (`more.personal`), потому что страница — публичная константа.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ADMIN_CHAT = 123456789


def _admin(conn):
    """Админ уже есть в тестовой базе — досоздаём только недостающее."""
    row = conn.execute("select id from participant where tg_chat_id = %s",
                       (ADMIN_CHAT,)).fetchone()
    if row is None:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,'Тест Админ',%s,'admin','active') returning id",
            (ADMIN_CHAT, make_key("Тест Админ"))).fetchone()[0]
    else:
        pid = row["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'admin') "
                 "on conflict do nothing", (pid,))
    conn.execute("insert into participant (tg_chat_id, display_name, person_key, role, status) "
                 "values (777,'Ждущий',%s,'troupe','pending')", (make_key("Ждущий"),))
    conn.execute("insert into feedback (kind, text, status) values ('bug','проверка','new')")
    conn.commit()
    return pid


def test_хаб_отдаёт_блоки_и_счётчики(client):
    """Контракт хаба после переделки (план 94, Ш-2/Ш-7): блоки, а не разделы с плитками.

    Проверяем то, ради чего тест и был: админ видит «требует внимания» со счётчиками
    и «Управление» со своими разделами, а недоступное не приходит вовсе.
    """
    _api()  # выставляет BOT_TOKEN для проверки initData
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        _admin(conn)
    auth = client.post("/webapp/auth", json={"init_data": init_data(ADMIN_CHAT)})
    assert auth.status_code == 200, auth.text
    token = auth.json()["token"]

    response = client.get("/webapp/home/data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True

    attention = body["attention"]
    assert attention, "хаб пуст: ни одного блока «требует внимания»"
    urls = {row["url"] for row in attention}
    assert "/webapp/admin/t/pending" in urls, "не пришёл блок заявок"
    assert body["counts"]["pending"] >= 1
    assert body["counts"]["feedback"] >= 1

    # Бейдж «Крупные суммы, ждут подтверждения» убран решением 30.10.2026: механизма
    # второго подтверждения нет, счётчик в approval_request не мог быть непустым.
    assert "approvals" not in body["counts"], "вернулся счётчик крупных сумм"
    assert all(row["title"] != "Крупные суммы" for row in attention), \
        "вернулся бейдж «Крупные суммы, ждут подтверждения»"

    # Бейдж «ждут решения» живёт в блоке внимания у раздела «Заявки», а не на «Людях»:
    # иначе человек жмёт раздел со счётчиком и не находит там действий
    pending = next(row for row in attention if row["url"] == "/webapp/admin/t/pending")
    assert pending["meta"] == "ждут решения", "нет счётчика заявок в блоке внимания"
    assert pending["count"] >= 1

    management = {row["url"] for row in body["more"]["management"]}
    assert "/webapp/admin/t/people" in management, "в «Управлении» нет людей"
    assert body["staff"] is True

    # «Личное»: строки едут данными, потому что страница — публичная константа.
    # Заметки — личный раздел у всех (FR-F7), файлы — по `files.access` (V-039 (в)).
    personal = body["more"]["personal"]
    assert [row["url"] for row in personal] == ["/webapp/files", "/webapp/notes"], personal
    assert personal[0]["title"] == "Файлы студии"
    assert personal[0]["meta"] == "записи, фонограммы, тексты"
    assert personal[1]["title"] == "Заметки"


def test_у_студийца_личное_без_файлов(client):
    """Файлы открыты по `files.access` (V-039 (в)), заметки — по `notes.write` (FR-F7).

    У студийца `files.access` снят миграцией 062: решение владельца — раздел видят
    admin, org и master, и «нет права» студиец читает словами, а не пустым списком.
    """
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,'Тест Студиец',%s,'troupe','active') returning id",
            (770500, make_key("Тест Студиец"))).fetchone()["id"]
        # Возможности берутся из participant_role, а не из participant.role: без строки
        # роли у человека «Возможностей: 0» и заметок он не увидит (та же причина, что
        # в обращении №24).
        conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                     (pid,))
        conn.commit()

    auth = client.post("/webapp/auth", json={"init_data": init_data(770500)})
    assert auth.status_code == 200, auth.text
    token = auth.json()["token"]

    response = client.get("/webapp/home/data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    personal = response.json()["more"]["personal"]
    assert [row["url"] for row in personal] == ["/webapp/notes"], personal
    assert all(row["url"] != "/webapp/files" for row in personal), "студийцу файлы не открыты"

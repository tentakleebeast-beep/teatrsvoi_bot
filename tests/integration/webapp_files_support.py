"""Общая обвязка HTTP-тестов файлов E4: подпись входа и участник с правом `files.access`.

Тесты Mini App ходят тем же путём, что браузер: `initData` → `/webapp/auth` → токен →
ручка. Токен подписывает тестовый бот, поэтому `core.BOT_TOKEN` выставляется здесь же
(без него `/webapp/auth` отвечает 401, и тест падает `KeyError: 'token'`).

Обоснование: docs/development/192 §6, §7 (право `files.access` проверяет сервер).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data


def participant(chat_id: int = ADMIN_CHAT, *, name: str = "Тест Файлов") -> int:
    """Действующий участник с точечным правом `files.access` (своё, не роль)."""
    from app import core

    core.BOT_TOKEN = BOT_TOKEN
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s, %s, %s, 'troupe', 'active')"
            " on conflict (tg_chat_id) do update set status = 'active'"
            " returning id", (chat_id, name, f"test-{chat_id}")).fetchone()
        conn.execute(
            "insert into participant_capability_override"
            " (participant_id, capability, allowed, reason) values (%s,'files.access',true,%s)"
            " on conflict (participant_id, capability) do update set allowed = true",
            (int(row[0]), "тестовая фикстура"))
        conn.commit()
        return int(row[0])


def headers(client, chat_id: int = ADMIN_CHAT) -> dict:
    """Заголовок с токеном Mini App для участника (создаёт его при необходимости)."""
    participant(chat_id)
    answer = client.post("/webapp/auth", json={"init_data": init_data(chat_id)})
    assert answer.status_code == 200, answer.text
    return {"Authorization": f"Bearer {answer.json()['token']}"}


def file_row(file_id: int) -> dict | None:
    """Строка файла как в БД — для проверок, которые не видны в ответе ручки."""
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute("select * from file_object where id = %s", (file_id,)).fetchone()

"""Общие помощники интеграционных тестов опросов (E8, docs/development/162 §14.7).

Обоснование: FR-G18, `docs/development/162-bot-polls.md` §14.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    """Действующий участник с ролью; роль нужна для прав из `v_participant_capabilities`."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s) "
                 "on conflict do nothing", (pid, role))
    conn.commit()
    return pid


def token(client, chat_id: int) -> dict:
    """Заголовок Mini App: обмен подписанного initData на короткий токен."""
    _api()
    answer = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()
    return {"Authorization": f"Bearer {answer['token']}"}


def tools(chat_id: int) -> dict:
    """Заголовок вызова tool-api из gateway: сервисный токен и человек."""
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


QUESTIONS = [
    {"position": 1, "text": "Какие файлы открываете утром?", "kind": "single",
     "required": True, "free_text": False, "max_choices": None,
     "options": [{"label": "Расписание"}, {"label": "Звонки"}]},
    {"position": 2, "text": "Сколько часов уходит на согласование?", "kind": "number",
     "required": True, "free_text": False, "max_choices": None, "options": []},
    {"position": 3, "text": "Что улучшить?", "kind": "text", "required": False,
     "free_text": False, "max_choices": None, "options": []},
]


def create_poll(client, headers: dict, questions: list[dict] | None = None,
                title: str = "Анкета организатору") -> int:
    response = client.post("/webapp/polls/save", headers=headers,
                           json={"title": title, "questions": questions or QUESTIONS})
    assert response.status_code == 200, response.text
    return response.json()["poll_id"]


def audience(client, headers: dict, poll_id: int, rules: list[dict],
             include: list[int] | None = None, exclude: list[int] | None = None,
             viewers: dict | None = None) -> dict:
    response = client.post("/webapp/polls/audience", headers=headers,
                           json={"poll_id": poll_id, "rules": rules,
                                 "include": include or [], "exclude": exclude or [],
                                 "viewers": viewers or {}})
    assert response.status_code == 200, response.text
    return response.json()


def notifications() -> list[dict]:
    """Записи очереди опросов: приглашение и напоминания (`kind like 'poll%'`)."""
    from psycopg.rows import dict_row

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "select n.*, p.tg_chat_id from notification n "
            "join participant p on p.id = n.recipient_id "
            "where n.kind in ('poll', 'poll_reminder') order by n.id").fetchall()
    return [dict(row) for row in rows]

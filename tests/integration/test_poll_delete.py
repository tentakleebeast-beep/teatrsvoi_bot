"""Удаление черновика опроса — V-136 (решение владельца 23.11.2026).

Правило: удаляется только черновик и только свой (или любой при `poll.results.all`).
Отправленный опрос не удаляется — в нём чужие ответы; прохождение у черновика тоже
стоп. Дочерние строки уходят каскадом, поэтому проверяем их отсутствие, а не код.

Проверяется на данных, а не на глаз: строка `poll`, вопросы, варианты, адресные
правила и запись `poll.delete` в аудите.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import QUESTIONS, audience, person, token

TITLE = "Черновик на удаление"

#: Номера чатов этого файла. Держим их отдельными от соседних файлов и убираем за собой:
#: `test_poll_interview_draft` проверяет, что таблицы опросов пусты, и чужие строки
#: превращают его в падение на ровном месте (V-136).
CHATS = (794101, 794102, 794103)


@pytest.fixture(autouse=True)
def _cleanup() -> None:
    """Убрать за собой: опросы и людей этого файла, чтобы не портить соседние тесты."""
    yield
    with psycopg.connect(_test_dsn()) as conn:
        ids = [row[0] for row in conn.execute(
            "select id from participant where tg_chat_id = any(%s)", (list(CHATS),)).fetchall()]
        if ids:
            conn.execute("delete from notification where recipient_id = any(%s)", (ids,))
            conn.execute("delete from poll where created_by = any(%s)", (ids,))
            conn.execute("delete from participant where id = any(%s)", (ids,))


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _scalar(sql: str, params: tuple = ()):
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _people() -> dict[str, int]:
    with psycopg.connect(_test_dsn()) as conn:
        return {
            "author": person(conn, 794101, "Удаление Автор", "org"),
            "alien": person(conn, 794102, "Удаление Чужой", "org"),
            "actor": person(conn, 794103, "Удаление Актёр", "troupe"),
        }


def _draft(client, headers: dict, title: str = TITLE) -> int:
    response = client.post("/webapp/polls/save", headers=headers,
                           json={"title": title, "questions": QUESTIONS})
    assert response.status_code == 200, response.text
    return response.json()["poll_id"]


def _delete(client, headers: dict, poll_id: int):
    return client.post("/webapp/polls/delete", headers=headers, json={"poll_id": poll_id})


def test_черновик_удаляется_вместе_с_детьми_и_пишет_аудит(client) -> None:
    """Черновик исчезает целиком; в аудите одна строка `poll.delete` с числом вопросов."""
    ids = _people()
    author = token(client, 794101)
    poll_id = _draft(client, author)
    audience(client, author, poll_id, rules=[{"kind": "role", "role": "troupe"}],
             viewers={"people": [], "roles": []})
    questions_before = _scalar("select count(*) from poll_question where poll_id = %s", (poll_id,))
    assert questions_before > 0

    response = _delete(client, author, poll_id)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "poll_id": poll_id}

    assert _scalar("select count(*) from poll where id = %s", (poll_id,)) == 0
    assert _scalar("select count(*) from poll_question where poll_id = %s", (poll_id,)) == 0
    assert _scalar("select count(*) from poll_audience where poll_id = %s", (poll_id,)) == 0

    audit = _rows("select action, entity_type, entity_id, before from audit_event "
                  "where action = 'poll.delete' order by id desc limit 1")
    assert len(audit) == 1
    assert audit[0]["entity_type"] == "poll" and audit[0]["entity_id"] == str(poll_id)
    assert audit[0]["before"]["questions"] == questions_before
    assert ids["author"]  # набор людей создан, иначе падало бы выше


def test_отправленный_опрос_не_удаляется(client) -> None:
    """409 `poll_not_draft`: ответы людей дороже порядка в списке."""
    ids = _people()
    author = token(client, 794101)
    poll_id = _draft(client, author)
    audience(client, author, poll_id, rules=[], include=[ids["author"]])
    started = client.post("/webapp/polls/start", headers=author, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text

    response = _delete(client, author, poll_id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "poll_not_draft"
    assert _scalar("select count(*) from poll where id = %s", (poll_id,)) == 1


def test_чужой_черновик_не_удаляется(client) -> None:
    """403 `alien_poll`: чужой черновик не трогают даже с `poll.manage`."""
    _people()
    poll_id = _draft(client, token(client, 794101))

    response = _delete(client, token(client, 794102), poll_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "alien_poll"
    assert _scalar("select count(*) from poll where id = %s", (poll_id,)) == 1


def test_без_права_удалять_нельзя(client) -> None:
    """403 `no_poll_manage`: у актёра права на опросы нет вовсе."""
    _people()
    poll_id = _draft(client, token(client, 794101))

    response = _delete(client, token(client, 794103), poll_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "no_poll_manage"
    assert _scalar("select count(*) from poll where id = %s", (poll_id,)) == 1

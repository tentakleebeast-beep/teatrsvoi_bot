"""Права опросов: poll.manage только у admin, результаты — автор, poll.results.all и poll_viewer (AC-9).

Отрицательный случай обязателен: `org` с `files.access`, но без `poll.manage`,
получает 403 и на список, и на сохранение — «право есть» и «возможность есть» здесь
не одно и то же (§14.3). Отдельно проверяется, что `poll.manage` не открывает чужие
результаты, а `poll_viewer` открывает конкретному человеку.

Обоснование: FR-G18, AC-9, `docs/development/162-bot-polls.md` §14.3.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import audience, create_poll, person, token


def _admin(chat_id: int, name: str = "Опрос Админ") -> None:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, chat_id, name, "admin")


def _other(client, chat_id: int, name: str, role: str) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, chat_id, name, role)


def test_организатор_без_poll_manage_получает_отказ(client):
    """`org` — не админ: списка нет, сохранения нет, код отказа машинный."""
    _other(client, 784011, "Опрос Орг", "org")
    headers = token(client, 784011)

    listing = client.get("/webapp/polls/list", headers=headers)
    assert listing.status_code == 403, listing.text
    assert "poll.manage" in listing.json()["detail"]

    saved = client.post("/webapp/polls/save", headers=headers,
                        json={"title": "Свой опрос", "questions": []})
    assert saved.status_code == 403, saved.text

    page = client.get("/webapp/nav", headers=headers)
    assert page.json()["screens"]["/webapp/polls"] is False


def test_админ_создаёт_и_видит_свой_опрос(client):
    _admin(784012)
    headers = token(client, 784012)

    poll_id = create_poll(client, headers)
    listing = client.get("/webapp/polls/list", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = listing.json()["polls"]
    assert [row["id"] for row in rows] == [poll_id]
    assert rows[0]["questions"] == 3 and rows[0]["status"] == "draft"


def test_результаты_видны_автору_и_poll_viewer_но_не_постороннему(client):
    _admin(784013)
    author = token(client, 784013)
    poll_id = create_poll(client, author)

    _other(client, 784014, "Опрос Зритель", "troupe")
    _other(client, 784015, "Опрос Посторонний", "troupe")
    with psycopg.connect(_test_dsn()) as conn:
        viewer_id = conn.execute("select id from participant where tg_chat_id = 784014"
                                 ).fetchone()[0]
    audience(client, author, poll_id, rules=[], viewers={"people": [viewer_id], "roles": []})

    own = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=author)
    assert own.status_code == 200, own.text
    assert own.json()["invited"] == 0

    viewer = token(client, 784014)
    allowed = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=viewer)
    assert allowed.status_code == 200, allowed.text

    stranger = token(client, 784015)
    denied = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=stranger)
    assert denied.status_code == 403, denied.text
    assert "автор" in denied.json()["detail"]

    export = client.get(f"/webapp/polls/export?poll_id={poll_id}&format=csv",
                        headers=stranger)
    assert export.status_code == 403, export.text

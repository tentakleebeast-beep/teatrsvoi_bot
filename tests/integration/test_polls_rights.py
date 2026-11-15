"""Права опросов: `poll.manage` у admin и org, результаты — автор, poll.results.all и poll_viewer (AC-9).

Ответ V-088 — «(б) админ и организатор»: организатор ведёт опросы сам (репро-тест
`test_V_088_...`), но чужие результаты ему не открыты — `poll.results.all` не выдан
(162 §3 D8). Роли без `poll.manage` получают отказ словами, а не пустой экран.
`poll_viewer` открывает результаты конкретному человеку.

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


def test_V_088_организатор_открывает_создаёт_и_сохраняет_опрос(client):
    """V-088 (б): `org` — не админ, но право `poll.manage` у него есть: экран, список, сохранение."""
    _other(client, 784011, "Опрос Орг", "org")
    headers = token(client, 784011)

    page = client.get("/webapp/nav", headers=headers)
    assert page.json()["screens"]["/webapp/polls"] is True

    listing = client.get("/webapp/polls/list", headers=headers)
    assert listing.status_code == 200, listing.text

    poll_id = create_poll(client, headers, title="Свой опрос организатора")
    assert poll_id > 0

    draft = client.get(f"/webapp/polls/edit/data?poll_id={poll_id}", headers=headers)
    assert draft.status_code == 200, draft.text
    assert draft.json()["poll"]["title"] == "Свой опрос организатора"
    assert draft.json()["can_edit"] is True

    rows = client.get("/webapp/polls/list", headers=headers).json()["polls"]
    assert [row["id"] for row in rows] == [poll_id]


def test_V_088_организатор_не_видит_чужие_результаты(client):
    """D8 (162 §3): чужой опрос организатору не открыт — `poll.results.all` ему не выдан."""
    _admin(784020)
    author = token(client, 784020)
    alien_poll = create_poll(client, author)

    _other(client, 784021, "Опрос Чужой Орг", "org")
    other_org = token(client, 784021)

    edit = client.get(f"/webapp/polls/edit/data?poll_id={alien_poll}", headers=other_org)
    assert edit.status_code == 403, edit.text
    results = client.get(f"/webapp/polls/results/data?poll_id={alien_poll}",
                         headers=other_org)
    assert results.status_code == 403, results.text


def test_роли_без_poll_manage_видят_отказ_словами_а_не_пустой_экран(client):
    """`troupe`/`master`: на списке и на сохранении — 403 с текстом про право."""
    _other(client, 784016, "Опрос Актёр", "troupe")
    _other(client, 784017, "Опрос Педагог", "master")
    for chat_id in (784016, 784017):
        headers = token(client, chat_id)
        listing = client.get("/webapp/polls/list", headers=headers)
        assert listing.status_code == 403, listing.text
        detail = listing.json()["detail"]
        assert "poll.manage" in detail and "организатор" in detail, detail

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

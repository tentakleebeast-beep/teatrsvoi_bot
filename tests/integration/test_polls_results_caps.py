"""Кто видит результаты опроса: `poll.results.all`, `poll_viewer` ролью и человеком (AC-9).

Что закрывает: прежний набор проверял автора, человека из `poll_viewer` и постороннего.
Не проверенными остались два носителя права из `db/init/065_polls.sql`:

* админ, который опрос не создавал, — видит результаты по `poll.results.all`;
* человек роли из `poll_viewer` (здесь `org`) — видит, а человек другой роли — нет.

Обоснование: FR-G18, AC-9, `docs/development/162-bot-polls.md` §14.3, §7.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import audience, create_poll, person, token


def _people() -> None:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784041, "Аккаунт Автор", "admin")
        person(conn, 784042, "Аккаунт Второй Админ", "admin")
        person(conn, 784043, "Аккаунт Орг", "org")
        person(conn, 784044, "Аккаунт Актёр", "troupe")


def test_админ_без_авторства_видит_чужие_результаты(client) -> None:
    """`poll.results.all` — отдельная возможность: автор не нужен."""
    _people()
    author = token(client, 784041)
    poll_id = create_poll(client, author)

    other = token(client, 784042)
    results = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=other)
    assert results.status_code == 200, results.text
    assert results.json()["poll"]["id"] == poll_id

    export = client.get(f"/webapp/polls/export?poll_id={poll_id}&format=md", headers=other)
    assert export.status_code == 200, export.text


def test_роль_из_poll_viewer_видит_результаты(client) -> None:
    """`poll_viewer` умеет пускать ролью, а не только человеком: org видит, troupe нет."""
    _people()
    author = token(client, 784041)
    poll_id = create_poll(client, author)
    audience(client, author, poll_id, rules=[], viewers={"people": [], "roles": ["org"]})

    org = token(client, 784043)
    allowed = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=org)
    assert allowed.status_code == 200, allowed.text

    actor = token(client, 784044)
    denied = client.get(f"/webapp/polls/results/data?poll_id={poll_id}", headers=actor)
    assert denied.status_code == 403, denied.text
    assert "no_results" in denied.text, denied.text

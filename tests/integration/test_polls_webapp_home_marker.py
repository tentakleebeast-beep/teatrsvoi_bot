"""Маркер «Мои опросы» на Главной считается из настоящей базы (E8-D4).

Стенд `tests/webapp/server.py` отдаёт три готовых формы ответа, поэтому маркер там
проверен только в разметке. Здесь тот же `/webapp/home/data` считается живым
`_polls_block` → `poll_store.my_runs`: три состояния по AC-1, AC-4, AC-12
(`docs/development/164-miniapp-home-polls.md` §12.2, §12.6).

Обоснование: FR-G18, AC-1, AC-4, AC-12, §12.2, §12.6.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import (
    audience,
    create_poll,
    notifications,
    person,
    token,
)


def _people() -> None:
    """Админ-организатор и адресат-актёр: у второго своих опросов не создаёт никто."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 787001, "Главная Админ", "admin")
        person(conn, 787002, "Главная Актёр", "troupe")


def _home(client, headers) -> dict:
    answer = client.get("/webapp/home/data", headers=headers)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _sent(client) -> tuple[int, int]:
    """Отправить опрос труппе: номер опроса и прохождение актёра 787002."""
    _people()
    owner = token(client, 787001)
    poll_id = create_poll(client, owner)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    started = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    run_id = next(row["payload"]["run_id"] for row in notifications()
                  if row["tg_chat_id"] == 787002)
    return poll_id, run_id


def test_без_прохождений_маркера_нет(client) -> None:
    """AC-12: `polls = null` — строку «Мои опросы» на Главной рисовать не из чего."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 787002, "Главная Актёр", "troupe")

    assert _home(client, token(client, 787002))["polls"] is None


def test_один_непройденный_даёт_счётчик_прохождение_его_снимает(client) -> None:
    """AC-1: бейдж «1» и подпись «1 ждёт ответа»; AC-4: после прохождения бейджа нет."""
    _poll_id, run_id = _sent(client)
    actor = token(client, 787002)

    polls = _home(client, actor)["polls"]
    assert polls == {"count": 1, "open": 1, "done": 0, "meta": "1 ждёт ответа",
                     "url": "/webapp/polls/mine"}, polls

    # Проходим все три вопроса анкеты: single, number, необязательный text.
    first = client.post("/webapp/polls/answer", headers=actor,
                        json={"run_id": run_id, "action": "o", "code": "a1"})
    assert first.status_code == 200, first.text
    second = client.post("/webapp/polls/answer", headers=actor,
                         json={"run_id": run_id, "action": "text", "text": "4"})
    assert second.status_code == 200, second.text
    last = client.post("/webapp/polls/answer", headers=actor,
                       json={"run_id": run_id, "action": "s"})
    assert last.json()["done"] is True, last.text

    polls = _home(client, actor)["polls"]
    # Счётчик исчез (count 0), строка осталась: пройденный опрос должен открываться.
    assert polls == {"count": 0, "open": 0, "done": 1, "meta": "1 пройдено",
                     "url": "/webapp/polls/mine"}, polls
    assert polls["count"] == 0


def test_закрытый_непройденный_не_считается_ждущим(client) -> None:
    """§12.4: закрытый организатором опрос уходит из «Ждут ответа» и из бейджа."""
    poll_id, _run_id = _sent(client)
    owner = token(client, 787001)
    closed = client.post("/webapp/polls/close", headers=owner, json={"poll_id": poll_id})
    assert closed.status_code == 200, closed.text

    polls = _home(client, token(client, 787002))["polls"]
    assert polls["count"] == 0 and polls["open"] == 0, polls
    assert polls["meta"] == "", polls["meta"]

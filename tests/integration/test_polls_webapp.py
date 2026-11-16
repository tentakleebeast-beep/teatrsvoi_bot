"""«Мои опросы» в Mini App: шаг, ответ, группы и адресность (E8-D4).

Критерии приёмки `docs/development/164-miniapp-home-polls.md` §12.9: AC-3 (прошёл
все вопросы — опрос в «Пройденных»), AC-9 (чужой `run_id` — `404`), AC-10 (адресат
без `poll.manage` видит свои прохождения), AC-11 (одна строка не в двух группах),
AC-14 (`multi`: два выбранных, «Дальше», «Назад» показывает оба), AC-15 (`number`
не понят — вопрос не меняется, ответ не записан), AC-17 (перезапуск возвращает на
тот же вопрос с прежними ответами).

Обоснование: FR-G18, AC-3, AC-9, AC-10, AC-11, AC-14, AC-15, AC-17,
`docs/development/164-miniapp-home-polls.md` §12.3, §12.5, §12.9.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import (
    audience,
    create_poll,
    notifications,
    person,
    token,
    tools,
)

SINGLE_QUESTION = [
    {"position": 1, "text": "Какие файлы открываете утром?", "kind": "single",
     "required": True, "free_text": False, "max_choices": None,
     "options": [{"label": "Расписание"}, {"label": "Звонки"}]},
]

MULTI_QUESTIONS = [
    {"position": 1, "text": "Какие файлы открываете утром?", "kind": "single",
     "required": True, "free_text": False, "max_choices": None,
     "options": [{"label": "Расписание"}, {"label": "Звонки"}]},
    {"position": 2, "text": "Что мешает больше всего?", "kind": "multi",
     "required": True, "free_text": False, "max_choices": None,
     "options": [{"label": "Согласование"}, {"label": "Переписка"}, {"label": "Отчёты"}]},
    {"position": 3, "text": "Что оставить как есть?", "kind": "text", "required": False,
     "free_text": False, "max_choices": None, "options": []},
]

NUMBER_QUESTION = [
    {"position": 1, "text": "Сколько часов уходит на согласование?", "kind": "number",
     "required": True, "free_text": False, "max_choices": None, "options": []},
]


def _people() -> None:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 785001, "Опросы Админ", "admin")
        person(conn, 785002, "Опросы Актёр", "troupe")
        person(conn, 785003, "Опросы Другой", "troupe")


def _started(client, questions=None) -> tuple[int, int]:
    """Опрос отправлен труппе: возвращает номер опроса и прохождение актёра 785002."""
    _people()
    owner = token(client, 785001)
    poll_id = create_poll(client, owner, questions=questions)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])
    started = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    run_id = next(row["payload"]["run_id"] for row in notifications()
                  if row["tg_chat_id"] == 785002)
    return poll_id, run_id


def _groups(client, headers) -> dict:
    answer = client.get("/webapp/polls/mine/data", headers=headers)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_прохождение_в_приложении_заканчивается_группой_пройденных(client) -> None:
    """AC-3: ответил на все вопросы — «Ответы сохранены», опрос ушёл в «Пройденные».

    И AC-18 с другой стороны: пройденный опрос пропадает из `/tools/polls/mine`,
    иначе чат предлагал бы «Продолжить» законченное.
    """
    _poll_id, run_id = _started(client)
    actor = token(client, 785002)

    step = client.get(f"/webapp/polls/step?run_id={run_id}", headers=actor)
    assert step.status_code == 200, step.text
    assert step.json()["step"]["position"] == 1
    assert step.json()["step"]["kind"] == "single"

    first = client.post("/webapp/polls/answer", headers=actor,
                        json={"run_id": run_id, "action": "o", "code": "a1"})
    assert first.status_code == 200, first.text
    assert first.json()["step"]["position"] == 2

    second = client.post("/webapp/polls/answer", headers=actor,
                         json={"run_id": run_id, "action": "text", "text": "4"})
    assert second.json()["step"]["position"] == 3

    last = client.post("/webapp/polls/answer", headers=actor,
                       json={"run_id": run_id, "action": "s"})
    assert last.status_code == 200, last.text
    assert last.json()["done"] is True
    assert last.json()["step"]["position"] == 3 and last.json()["step"]["total"] == 3

    groups = _groups(client, actor)
    assert [row["run_id"] for row in groups["done"]] == [run_id]
    assert groups["open"] == []
    assert "пройден" in groups["done"][0]["meta"]

    # Чат больше не предлагает это прохождение: набор тот же, что до правки.
    mine = client.get("/tools/polls/mine", headers=tools(785002))
    assert mine.status_code == 200, mine.text
    assert mine.json()["runs"] == []


def test_чужое_прохождение_не_отдаётся(client) -> None:
    """AC-9: адресность решает `participant_id`, а не номер в адресе."""
    _poll_id, run_id = _started(client)
    other = token(client, 785003)

    step = client.get(f"/webapp/polls/step?run_id={run_id}", headers=other)
    assert step.status_code == 404, step.text
    assert step.json().get("code") == "run_not_found"

    answer = client.post("/webapp/polls/answer", headers=other,
                         json={"run_id": run_id, "action": "o", "code": "a1"})
    assert answer.status_code == 404, answer.text
    # Данные владельца прохождения не изменились.
    owner = client.get(f"/webapp/polls/step?run_id={run_id}", headers=token(client, 785002))
    assert owner.json()["step"]["position"] == 1
    assert owner.json()["step"]["chosen"] == []


def test_адресат_без_poll_manage_видит_свои_прохождения(client) -> None:
    """AC-10: экран открыт всем, данные — только свои; конструктор по-прежнему 403."""
    _poll_id, run_id = _started(client)
    actor = token(client, 785002)

    groups = _groups(client, actor)
    assert [row["run_id"] for row in groups["open"]] == [run_id]
    assert groups["open"][0]["url"] == f"/webapp/polls/answer?run_id={run_id}"

    # Организаторский список закрыт правом: адресат его не видит.
    denied = client.get("/webapp/polls/list", headers=actor)
    assert denied.status_code == 403, denied.text


def test_пройденный_и_закрытый_не_попадают_в_две_группы(client) -> None:
    """AC-11: `done` сильнее срока; закрытый без ответа — только в «Закрытых»."""
    first_poll, first_run = _started(client)
    actor = token(client, 785002)
    client.post("/webapp/polls/answer", headers=actor,
                json={"run_id": first_run, "action": "o", "code": "a1"})
    client.post("/webapp/polls/answer", headers=actor,
                json={"run_id": first_run, "action": "text", "text": "2"})
    client.post("/webapp/polls/answer", headers=actor,
                json={"run_id": first_run, "action": "s"})

    owner = token(client, 785001)
    second_poll = create_poll(client, owner, title="Опрос про зал")
    audience(client, owner, second_poll, rules=[{"kind": "role", "role": "troupe"}])
    client.post("/webapp/polls/start", headers=owner, json={"poll_id": second_poll})
    closed_run = next(row["payload"]["run_id"] for row in notifications()
                      if row["payload"].get("poll_id") == second_poll
                      and row["tg_chat_id"] == 785002)
    closed = client.post("/webapp/polls/close", headers=owner, json={"poll_id": second_poll})
    assert closed.status_code == 200, closed.text

    groups = _groups(client, actor)
    assert [row["run_id"] for row in groups["done"]] == [first_run]
    assert [row["run_id"] for row in groups["closed"]] == [closed_run]
    assert groups["open"] == []
    assert "опрос закрыт" in groups["closed"][0]["meta"]
    assert first_poll != second_poll


def test_multi_выбор_дальше_и_назад_с_обоими(client) -> None:
    """AC-14: два выбранных варианта переживают «Дальше» и видны после «Назад»."""
    _poll_id, run_id = _started(client, questions=MULTI_QUESTIONS)
    actor = token(client, 785002)

    client.post("/webapp/polls/answer", headers=actor,
                json={"run_id": run_id, "action": "o", "code": "a1"})
    multi = client.post("/webapp/polls/answer", headers=actor,
                        json={"run_id": run_id, "action": "o", "code": "a1"})
    assert multi.json()["step"]["position"] == 2
    second = client.post("/webapp/polls/answer", headers=actor,
                         json={"run_id": run_id, "action": "o", "code": "a2"})
    assert second.json()["step"]["chosen"] == ["a1", "a2"]

    forward = client.post("/webapp/polls/answer", headers=actor,
                          json={"run_id": run_id, "action": "n"})
    assert forward.json()["step"]["position"] == 3

    back = client.post("/webapp/polls/answer", headers=actor,
                       json={"run_id": run_id, "action": "b"})
    assert back.json()["step"]["position"] == 2
    assert back.json()["step"]["chosen"] == ["a1", "a2"]


def test_неверное_число_оставляет_вопрос_и_не_пишет_ответ(client) -> None:
    """AC-15: «четыре» — сообщение под полем, вопрос тот же, ответа в базе нет."""
    _poll_id, run_id = _started(client, questions=NUMBER_QUESTION)
    actor = token(client, 785002)

    retry = client.post("/webapp/polls/answer", headers=actor,
                        json={"run_id": run_id, "action": "text", "text": "четыре"})
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["retry"] is True
    assert body["message"] == "Не понял число. Например: 4"
    assert body["step"]["position"] == 1
    assert body["step"]["kind"] == "number"

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = conn.execute("select * from poll_answer where run_id = %s",
                            (run_id,)).fetchall()
    assert rows == []


def test_перезапуск_возвращает_на_тот_же_вопрос(client) -> None:
    """AC-17: `position` живёт в базе, а не в памяти страницы."""
    _poll_id, run_id = _started(client)
    actor = token(client, 785002)
    client.post("/webapp/polls/answer", headers=actor,
                json={"run_id": run_id, "action": "o", "code": "a2"})

    # Новый запуск приложения: адрес тот же, `position` не передаётся.
    again = client.get(f"/webapp/polls/step?run_id={run_id}", headers=actor)
    assert again.json()["step"]["position"] == 2

    # «Назад» показывает прежний ответ отмеченным.
    back = client.post("/webapp/polls/answer", headers=actor,
                       json={"run_id": run_id, "action": "b"})
    assert back.json()["step"]["position"] == 1
    assert back.json()["step"]["chosen"] == ["a2"]

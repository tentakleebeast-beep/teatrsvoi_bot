"""Приглашение на опрос из вопросов `text`: кнопка ответа в первом же сообщении (дефект).

Дефект. `api/polls.py:_invitation_markup` собирает кнопки только для `single`, `multi`
и `yesno`. У вопроса `text` (и `number`) вариантов нет, поэтому приглашение уходит
с пустым `inline_keyboard`, а в чате нет и второго входа: состояние `PollAnswer.await_text`
ставит единственная кнопка «Другое — напишу» (`p:f`), которой у такого вопроса нет
или которую не собирает `poll_ui.keyboard_rows`. Итог: человек получает вопрос
«Ответьте сообщением», отвечает — и ответ уходит в `fallback.state_fallback_text`.

Почему это про анкету 161: все 35 вопросов там `text` (162 §9), то есть приглашение
на каждый из четырёх опросов приходит без единой кнопки.

Репро падает на текущем коде и должен проходить после правки: у приглашения на вопрос
со свободным вводом обязана быть кнопка, открывающая этот вопрос.

Обоснование: FR-G18, AC-4, AC-6, `docs/development/162-bot-polls.md` §9, §14.6.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import audience, create_poll, notifications, person, token

TEXT_QUESTION = [{"position": 1, "text": "Какие файлы вы открываете утром?", "kind": "text",
                  "required": True, "free_text": False, "max_choices": None, "options": []}]


def test_приглашение_на_вопрос_текста_несёт_кнопку(client) -> None:
    """Пустой `inline_keyboard` — человек не может начать отвечать из приглашения."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784051, "Текст Админ", "admin")
        person(conn, 784052, "Текст Актёр", "troupe")
    owner = token(client, 784051)
    poll_id = create_poll(client, owner, questions=TEXT_QUESTION)
    audience(client, owner, poll_id, rules=[{"kind": "role", "role": "troupe"}])

    started = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text

    row = notifications()[0]
    markup = row["payload"]["markup"]
    buttons = [button for line in markup["inline_keyboard"] for button in line]
    assert buttons, (
        "приглашение на вопрос типа text ушло без кнопок: состояние свободного ответа "
        "выставить нечем, ответ человека уйдёт в заглушку")
    assert any(button["callback_data"].startswith("p:") for button in buttons)
    # Лимит Telegram — 18 байт на callback_data (162 §5.2): кнопка приглашения
    # собирается из run_id и позиции, поэтому проверяется вместе с остальными.
    for button in buttons:
        size = len(button["callback_data"].encode())
        assert size <= 18, f"callback_data {button['callback_data']!r} — {size} байт, лимит 18"

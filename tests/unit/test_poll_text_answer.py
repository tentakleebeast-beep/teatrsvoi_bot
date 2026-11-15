"""Вопрос `text`/`number` в чате: человеку есть чем ответить (E8, дефект).

Дефект. У вопроса `text` и `number` вариантов нет, а кнопка «Другое — напишу» (`p:f`,
единственный вход в `PollAnswer.await_text`) собирается по флагу `free_text`, который
у этих типов не ставится. `keyboard_rows` возвращает пустой список, сообщение говорит
«Ответьте сообщением», но свободный текст ловит `fallback.state_fallback_text`, а не
опрос: ответ человека теряется. Заодно приглашение (`api/polls.py:_invitation_markup`)
для такого вопроса уходит с пустым `inline_keyboard`.

Почему это не мелочь: анкета `docs/development/161` — 35 вопросов из 35 типа `text`,
ради неё фича и делалась (162 §9). Пока кнопки нет, весь прогон 161 в чате не проходится.

Репро падает на текущем коде и должен проходить после правки: у вопроса, который
принимает свободный ввод, обязана быть кнопка, переводящая состояние в `await_text`.

Обоснование: FR-G18, AC-6, AC-8, `docs/development/162-bot-polls.md` §14.4 (D4),
`docs/development/161` §3–§6.
"""

from __future__ import annotations

import gateway_modules

POLL_UI = gateway_modules.load("poll_ui")

STEP = {
    "run_id": 1_234_567, "position": 2, "total": 11, "title": "Анкета организаторам",
    "kind": "text", "text": "Какие файлы вы открываете утром?", "required": True,
    "free_text": False, "max_choices": None, "options": [],
    "chosen": [], "chosen_text": None,
}


def _actions(step: dict) -> list[str]:
    """Действия, до которых человек может добраться кнопками шага."""
    parsed = (POLL_UI.parse(data)
              for row in POLL_UI.keyboard_rows(step) for _label, data in row)
    return [part["action"] for part in parsed if part]


def test_вопрос_текста_даёт_кнопку_свободного_ответа():
    """Без кнопки `f` состояние `await_text` не выставится и текст уйдёт в заглушку."""
    assert "f" in _actions(STEP), (
        "у вопроса text нет кнопки свободного ответа: сообщение просит ответить "
        "сообщением, но диалог опроса такое сообщение не ловит")


def test_вопрос_числа_даёт_кнопку_свободного_ответа():
    """Число вводится тем же путём: сообщением, и состояние ставит та же кнопка."""
    assert "f" in _actions(dict(STEP, kind="number")), (
        "у вопроса number нет кнопки свободного ответа")

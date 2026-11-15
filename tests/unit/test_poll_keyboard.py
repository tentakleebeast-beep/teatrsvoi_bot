"""Кнопки шага и текст сообщения: `callback_data` ≤ 18 байт, прогресс, «Назад» (AC-6, AC-7).

Лимит проверяется до Telegram: семизначный `run` и двузначный код варианта — худший
случай §14.4. «Пропустить» есть только у необязательного вопроса, «Назад» — везде,
кроме первого. Прогресс — словами в первой строке: «вопрос N из M».

Обоснование: FR-G18, AC-4, AC-6, AC-7, `docs/development/162-bot-polls.md` §14.4.
"""

from __future__ import annotations

import gateway_modules
import pytest

POLL_UI = gateway_modules.load("poll_ui")

STEP = {
    "run_id": 1_234_567, "position": 2, "total": 11, "title": "Анкета организаторам",
    "kind": "single", "text": "Как выбрать окно?", "required": True, "free_text": False,
    "max_choices": None,
    "options": [{"code": "a1", "label": "Расписание"}, {"code": "a2", "label": "Звонки"}],
    "chosen": [], "chosen_text": None,
}


def test_код_варианта_влезает_в_восемнадцать_байт():
    """Худший случай: семизначный run и двузначный код варианта."""
    data = POLL_UI.callback("o", 1_234_567, 2, "a1")
    assert data == "p:o:1234567:2:a1"
    assert len(data.encode()) <= POLL_UI.CB_LIMIT == 18


@pytest.mark.parametrize("action", ["n", "y", "x", "b", "s", "f"])
def test_остальные_коды_короче(action):
    data = POLL_UI.callback(action, 1_234_567, 2)
    assert len(data.encode()) <= POLL_UI.CB_LIMIT


def test_продолжить_из_меню_без_позиции():
    assert POLL_UI.callback("q", 42) == "p:q:42"


def test_разбор_кода_и_чужой_префикс():
    assert POLL_UI.parse("p:o:41:2:a1") == {"action": "o", "run_id": 41, "position": 2,
                                            "code": "a1"}
    assert POLL_UI.parse("p:q:41") == {"action": "q", "run_id": 41, "position": None,
                                       "code": None}
    assert POLL_UI.parse("av:day:0") is None
    assert POLL_UI.parse("p:zzz:41") is None


def test_прогресс_словами_первой_строкой():
    text = POLL_UI.question_text(STEP)
    assert text.splitlines()[0] == "Анкета организаторам · вопрос 2 из 11"
    assert "Как выбрать окно?" in text


def test_пропустить_только_у_необязательного_и_назад_не_на_первом():
    rows = POLL_UI.keyboard_rows(STEP)
    labels = [label for row in rows for label, _ in row]
    assert "Назад" in labels
    assert "Пропустить" not in labels

    optional = dict(STEP, required=False)
    labels = [label for row in POLL_UI.keyboard_rows(optional) for label, _ in row]
    assert "Пропустить" in labels

    first = dict(STEP, position=1)
    labels = [label for row in POLL_UI.keyboard_rows(first) for label, _ in row]
    assert "Назад" not in labels


def test_выбранное_показывается_словами():
    """Возврат на отвеченный вопрос: «выбрано: …», иначе человек не видит свой ответ."""
    chosen = dict(STEP, chosen=["a2"])
    assert "выбрано: Звонки" in POLL_UI.question_text(chosen)


def test_мультивыбор_даёт_кнопку_дальше_а_свободный_ответ_свою():
    multi = dict(STEP, kind="multi", free_text=True)
    labels = [label for row in POLL_UI.keyboard_rows(multi) for label, _ in row]
    assert "Дальше" in labels
    assert "Другое — напишу" in labels

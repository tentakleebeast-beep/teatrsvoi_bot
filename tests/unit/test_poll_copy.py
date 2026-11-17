"""Копия опроса в чистой части: тема, наполнение и коды вариантов (166 §2).

Проверяется `poll_store.draft_copy` без базы и без FastAPI: правила переноса живут
в модуле правил, а маршрут только пишет строки. Без этой части весь уровень
закрывался бы integration-тестом (166 §5.5).

Обоснование: FR-G18, AC-15, AC-16, AC-19, AC-20,
`docs/development/166-poll-draft-copy.md` §2, §5.1, §5.5.
"""

from __future__ import annotations

import pytest
from app import poll_store

SOURCE = {
    "id": 7,
    "title": "Анкета организатору",
    "intro": "Общий блок организаторам",
    "anonymous": False,
    "closes_at": "2026-11-01T09:00:00+04:00",
    "started_at": "2026-11-02T09:00:00+04:00",
    "closed_at": "2026-11-15T18:00:00+04:00",
}

QUESTIONS = [
    {"id": 101, "position": 1, "text": "Какие файлы открываете утром?", "kind": "single",
     "required": True, "free_text": False, "max_choices": None,
     "options": [{"position": 1, "code": "c1", "label": "Расписание"},
                 {"position": 2, "code": "c2", "label": "Звонки"}]},
    {"id": 102, "position": 2, "text": "Сколько часов уходит на согласование?",
     "kind": "number", "required": True, "free_text": False, "max_choices": None,
     "options": []},
    {"id": 103, "position": 3, "text": "Что улучшить?", "kind": "text", "required": False,
     "free_text": False, "max_choices": None, "options": []},
]


def test_тема_копии_получает_суффикс() -> None:
    """Тема копии — тема источника плюс « — копия»; копия копии даёт второй суффикс."""
    draft = poll_store.draft_copy(SOURCE, QUESTIONS)
    assert draft["title"] == "Анкета организатору — копия"

    again = poll_store.draft_copy(dict(SOURCE, title=draft["title"]), QUESTIONS)
    assert again["title"] == "Анкета организатору — копия — копия"


def test_длинная_тема_срезается_до_предела_без_двойного_пробела() -> None:
    """AC-20: тема 300 знаков, 290-й — пробел: срез до 290, длина ≤ 300, один пробел.

    `clean_text(limit=…)` здесь не годится: он не обрезает, а отказывает `too_long`,
    и копия длинной темы падала бы на ровном месте (166 §2).
    """
    title = "а" * 289 + " " + "б" * 10
    assert len(title) == 300 and title[289] == " "
    draft = poll_store.draft_copy(dict(SOURCE, title=title), QUESTIONS)
    expected = poll_store.clean_text(title)[:290].rstrip() + " — копия"
    assert draft["title"] == expected
    assert len(draft["title"]) <= poll_store.TITLE_LIMIT
    assert draft["title"].endswith(" — копия")
    assert "  — копия" not in draft["title"], "двойной пробел перед тире"


def test_intro_и_anonymous_переносятся_а_сроки_нет() -> None:
    """D3: `intro` и `anonymous` — свойство анкеты; сроки, статус и автор — нет."""
    draft = poll_store.draft_copy(dict(SOURCE, anonymous=True), QUESTIONS)
    assert draft["intro"] == "Общий блок организаторам"
    assert draft["anonymous"] is True
    for key in ("closes_at", "started_at", "closed_at", "status", "created_by"):
        assert key not in draft, f"в копию переехало поле отправленного опроса: {key}"


def test_вопросы_переносятся_с_правилами_а_коды_пересчитываются() -> None:
    """AC-15, AC-16: правила вопросов те же, коды вариантов — из `next_codes`."""
    draft = poll_store.draft_copy(SOURCE, QUESTIONS)
    questions = draft["questions"]
    assert [item["position"] for item in questions] == [1, 2, 3]
    assert [item["kind"] for item in questions] == ["single", "number", "text"]
    assert [item["required"] for item in questions] == [True, True, False]
    assert [item["text"] for item in questions] == [item["text"] for item in QUESTIONS]
    options = questions[0]["options"]
    assert [option["label"] for option in options] == ["Расписание", "Звонки"]
    assert [option["code"] for option in options] == poll_store.next_codes(2)
    assert [option["code"] for option in options] == ["a1", "a2"]


def test_пустой_источник_отказывает_как_no_questions() -> None:
    """Копировать нечего — код `no_questions`: тем же кодом отвечает и `save`."""
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.draft_copy(SOURCE, [])
    assert error.value.code == "no_questions"
    assert error.value.text == "В опросе нет вопросов: копировать нечего."


def test_варианты_больше_предела_отказывает_как_у_save() -> None:
    """Второго набора правил нет: предел вариантов тот же `too_many_options`."""
    many = [{"id": 1, "position": 1, "text": "Выберите", "kind": "multi",
             "required": True, "free_text": False, "max_choices": None,
             "options": [{"label": f"Вариант {index}"}
                         for index in range(poll_store.MAX_OPTIONS + 1)]}]
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.draft_copy(SOURCE, many)
    assert error.value.code == "too_many_options"

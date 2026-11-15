"""Конструктор опроса: пять типов вопросов, коды вариантов, сохранение без потерь.

AC-2 (`docs/development/162-bot-polls.md` §14.7): опрос с пятью типами вопросов
сохраняется и читается без потерь, `position` и `code` стабильны, варианты не
теряются, `free_text` у `yesno`/`number`/`text` запрещён.

Обоснование: FR-G18, AC-2, `docs/development/162-bot-polls.md` §14.1, §14.2.
"""

from __future__ import annotations

import pytest
from app import poll_store


def _question(**overrides):
    item = {"position": 1, "text": "Вопрос", "kind": "single", "required": True,
            "free_text": False, "max_choices": None,
            "options": [{"label": "Раз"}, {"label": "Два"}]}
    item.update(overrides)
    return item


def test_пять_типов_вопросов_сохраняются_и_читаются_без_потерь():
    """Все пять типов проходят проверку; позиции и коды не меняются при повторной проверке."""
    raw = [
        _question(position=1, kind="single"),
        _question(position=2, kind="multi", options=[{"label": "A"}, {"label": "B"}]),
        _question(position=3, kind="yesno", options=[]),
        _question(position=4, kind="number", options=[], free_text=False),
        _question(position=5, kind="text", options=[], required=False),
    ]
    first = poll_store.validate_questions(raw)
    second = poll_store.validate_questions(raw)
    assert [item["kind"] for item in first] == ["single", "multi", "yesno", "number", "text"]
    assert [item["position"] for item in first] == [1, 2, 3, 4, 5]
    assert first == second
    assert [option["code"] for option in first[0]["options"]] == ["a1", "a2"]
    assert [option["label"] for option in first[1]["options"]] == ["A", "B"]
    assert first[2]["options"] == [] and first[3]["options"] == []


def test_свободный_ответ_запрещён_у_типов_без_списка():
    """`check (kind in ('single','multi') or free_text = false)` держит и код, и схема."""
    for kind in ("yesno", "number", "text"):
        with pytest.raises(poll_store.DraftError) as error:
            poll_store.validate_questions([_question(kind=kind, options=[], free_text=True)])
        assert error.value.code == "bad_free_text"


def test_неизвестный_тип_и_пустой_вопрос_дают_код_контракта():
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(kind="slider")])
    assert error.value.code == "bad_kind"
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(text="   ")])
    assert error.value.code == "empty_question"
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([])
    assert error.value.code == "no_questions"


def test_список_без_вариантов_не_сохраняется():
    """«Список без списка» — частая поломка конструкторов; отказ, а не пустой вопрос."""
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(options=[])])
    assert error.value.code == "no_options"


def test_коды_вариантов_двухбайтовые_и_переходят_на_следующую_букву():
    codes = poll_store.next_codes(11)
    assert codes[:9] == [f"a{index}" for index in range(1, 10)]
    assert codes[9:] == ["b1", "b2"]
    assert all(len(code) == 2 for code in codes)


def test_больше_двухсот_тридцати_четырёх_вариантов_отказ():
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.next_codes(poll_store.MAX_OPTIONS + 1)
    assert error.value.code == "too_many_options"


def test_число_принимается_с_запятой_и_точкой():
    """§14.4 п. 4: дробное число приходит и с запятой, и с точкой."""
    assert poll_store.parse_number("4") == 4
    assert float(poll_store.parse_number("4,5")) == 4.5
    assert float(poll_store.parse_number("4.5")) == 4.5
    assert poll_store.parse_number("четыре") is None
    assert poll_store.parse_number("") is None
    assert poll_store.parse_number("NaN") is None

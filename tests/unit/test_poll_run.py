"""Прохождение опроса: повторный выбор перезаписывает ответ, а не дублирует (AC-5, AC-6).

Одна строка на вопрос держится первичным ключом `poll_answer(run_id, question_id)`,
а правила выбора — `poll_store.merge_choice` и `answer_codes`. Здесь проверяются
именно они: «нажал другой вариант» и «нажал тот же в multi» — разные вещи.

Обоснование: FR-G18, AC-5, AC-6, `docs/development/162-bot-polls.md` §14.4.
"""

from __future__ import annotations

from app import poll_store


def test_single_заменяет_предыдущий_выбор():
    """Вторая кнопка не добавляет код, а меняет: строка ответа остаётся одна."""
    first = poll_store.merge_choice("single", [], "a1")
    second = poll_store.merge_choice("single", first, "a3")
    assert first == ["a1"]
    assert second == ["a3"]


def test_yesno_всегда_один_код():
    assert poll_store.merge_choice("yesno", ["y"], "n") == ["n"]
    assert poll_store.answer_codes("yesno", ["y", "n"]) == ["n"]


def test_multi_переключает_и_снимает_повторное_нажатие():
    codes = poll_store.merge_choice("multi", [], "a1")
    codes = poll_store.merge_choice("multi", codes, "a2")
    assert codes == ["a1", "a2"]
    codes = poll_store.merge_choice("multi", codes, "a1")
    assert codes == ["a2"]


def test_multi_уважает_предел_и_чужой_код():
    """Код не из этого вопроса и лишний по `max_choices` не попадают в ответ."""
    codes = poll_store.merge_choice("multi", ["a1"], "zz", allowed=["a1", "a2"])
    assert codes == ["a1"]
    codes = poll_store.merge_choice("multi", ["a1", "a2"], "a3",
                                    allowed=["a1", "a2", "a3"], max_choices=2)
    assert codes == ["a1", "a2"]


def test_у_number_и_text_кодов_нет():
    assert poll_store.answer_codes("number", ["a1"]) == []
    assert poll_store.answer_codes("text", ["a1", "a2"]) == []
    assert poll_store.merge_choice("text", ["a1"], "a2") == []


def test_дубли_в_наборе_убираются():
    assert poll_store.answer_codes("multi", ["a1", "a1", "a2"]) == ["a1", "a2"]

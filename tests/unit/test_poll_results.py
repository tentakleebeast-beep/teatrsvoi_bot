"""Сводка по типам вопросов (AC-10): счётчики, «сумма больше ответивших», min/avg/max, тексты.

Формы ответа — `docs/development/162-bot-polls.md` §14.2 (`summary`). Для `multi`
сумма выборов больше числа ответивших — это нормально и подписано на экране,
а не поломка счётчика.

Обоснование: FR-G18, AC-10, `docs/development/162-bot-polls.md` §14.2.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

from app import poll_store

NOW = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _answer(**overrides):
    row = {"participant_id": 1, "display_name": "Аня", "option_codes": [], "text_value": None,
           "number_value": None, "skipped": False, "answered_at": NOW}
    row.update(overrides)
    return row


def test_single_считает_варианты_и_ответивших():
    question = {"id": 1, "position": 1, "kind": "single", "text": "Что?", "required": True,
                "free_text": False,
                "options": [{"code": "a1", "label": "Один"}, {"code": "a2", "label": "Два"}]}
    summary = poll_store.summarize(question, [
        _answer(option_codes=["a1"]), _answer(option_codes=["a1"], participant_id=2),
        _answer(option_codes=["a2"], participant_id=3),
        _answer(skipped=True, participant_id=4),
    ])
    assert summary["counts"] == [{"code": "a1", "label": "Один", "n": 2},
                                 {"code": "a2", "label": "Два", "n": 1}]
    assert summary["answered"] == 3
    assert summary["skipped"] == 1


def test_yesno_коды_фиксированы_и_подписи_да_нет():
    question = {"id": 2, "position": 2, "kind": "yesno", "text": "Да?", "required": True,
                "free_text": False, "options": []}
    summary = poll_store.summarize(question, [
        _answer(option_codes=["y"]), _answer(option_codes=["n"], participant_id=2)])
    assert summary["counts"] == [{"code": "y", "label": "Да", "n": 1},
                                 {"code": "n", "label": "Нет", "n": 1}]


def test_multi_считает_сумму_и_помечает_что_она_больше_ответивших():
    question = {"id": 3, "position": 3, "kind": "multi", "text": "Что?", "required": True,
                "free_text": False,
                "options": [{"code": "a1", "label": "Один"}, {"code": "a2", "label": "Два"}]}
    summary = poll_store.summarize(question, [
        _answer(option_codes=["a1", "a2"]), _answer(option_codes=["a1"], participant_id=2)])
    assert summary["answered"] == 2
    assert summary["sum"] == 3
    assert summary["sum_gt_answered"] is True


def test_number_даёт_min_avg_max_и_значения():
    question = {"id": 4, "position": 4, "kind": "number", "text": "Сколько?", "required": True,
                "free_text": False, "options": []}
    summary = poll_store.summarize(question, [
        _answer(number_value=2, display_name="Аня"),
        _answer(number_value=5, participant_id=2, display_name="Боря")])
    assert summary["min"] == 2
    assert summary["avg"] == 3.5
    assert summary["max"] == 5
    assert [item["display_name"] for item in summary["values"]] == ["Аня", "Боря"]


def test_text_даёт_список_с_автором_и_временем():
    question = {"id": 5, "position": 5, "kind": "text", "text": "Расскажите", "required": False,
                "free_text": False, "options": []}
    summary = poll_store.summarize(question, [_answer(text_value="Ок")])
    assert summary["answers"][0]["text"] == "Ок"
    assert summary["answers"][0]["display_name"] == "Аня"
    assert summary["answers"][0]["answered_at"].startswith("2026-11-15T10:00")


def test_анонимный_ответ_не_показывает_автора():
    """V-089: пока вопрос открыт, сводка показывает авторов; схема готова к флагу."""
    question = {"id": 6, "position": 6, "kind": "text", "text": "Расскажите", "required": False,
                "free_text": False, "options": []}
    rows = [_answer(text_value="Ок")]
    summary = poll_store.summarize(question, rows)
    assert summary["answers"][0]["display_name"] == "Аня"
    masked = [dict(row, participant_id=None, display_name=None) for row in rows]
    assert poll_store.summarize(question, masked)["answers"][0]["display_name"] is None

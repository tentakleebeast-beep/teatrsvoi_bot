"""Проверка формы набора A-0: 30 вопросов, классы, эталонные факты, отсутствие дублей.

Набор — это измерительная линейка (карта A-0, `docs/reports/112-agent-eval.md`): если
в нём пропадёт класс, дубль или факты, замер останется зелёным и будет врать.
Поэтому проверяем сам файл, а не прогон.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime

import agent_eval

SET = agent_eval.SET_PATH


def load() -> dict:
    with open(SET, encoding="utf-8") as fh:
        return json.load(fh)


def resolved(day: date = date(2026, 11, 1)) -> dict:
    """Набор с подставленной датой прогона: в файле лежат `{today_date}`."""
    return agent_eval.resolve_today(load(), day)


def test_set_has_thirty_questions():
    data = load()
    assert len(data["questions"]) == 30


def test_ids_are_unique_and_ordered():
    ids = [item["id"] for item in load()["questions"]]
    assert len(ids) == len(set(ids))
    assert ids == [f"Q-{number:02d}" for number in range(1, 31)]


def test_every_question_has_class_and_source():
    data = load()
    for item in data["questions"]:
        assert item["class"] in ("deterministic", "tool", "web"), item["id"]
        assert item["source"].strip(), item["id"]
        assert item["why"].strip(), item["id"]
        assert item["expect"] in ("answer", "refuse"), item["id"]


def test_all_three_classes_are_represented():
    counts = Counter(item["class"] for item in load()["questions"])
    assert counts["deterministic"] >= 10
    assert counts["tool"] >= 14
    assert counts["web"] >= 4


def test_answer_questions_have_machine_checkable_facts():
    for item in load()["questions"]:
        if item["expect"] != "answer":
            continue
        assert item["facts"], item["id"]
        for fact in item["facts"]:
            assert fact["kind"] in ("date", "time", "money", "phrase", "any", "weekday"), item["id"]
            assert fact["why"].strip(), item["id"]
            if fact["kind"] == "any":
                assert fact["value"], item["id"]
            else:
                assert fact["value"] not in (None, ""), item["id"]


def test_dates_and_times_in_facts_are_parseable():
    for item in resolved()["questions"]:
        for fact in item["facts"]:
            if fact["kind"] == "date":
                datetime.strptime(str(fact["value"]), "%Y-%m-%d")
            if fact["kind"] == "time":
                hour, minute = str(fact["value"]).split(":")
                assert 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59, item["id"]


def test_no_duplicate_question_texts():
    texts = [" ".join(item["text"].lower().split()) for item in load()["questions"]]
    assert len(texts) == len(set(texts))


def test_known_date_defect_is_first_in_set():
    """01.11.2026 бот ответил датой 2023 года — этот вопрос обязан быть и запрещать год."""
    question = load()["questions"][0]
    assert "сегодня число" in question["text"]
    assert question["class"] == "tool"
    assert "2023" in question["forbidden"]


def test_script_validator_accepts_set():
    assert agent_eval.problems(resolved()) == []


def test_today_placeholders_are_substituted_from_run_date():
    """Дата и день недели берутся от даты прогона, а не вбиты константой."""
    data = resolved(date(2026, 11, 2))
    q01 = data["questions"][0]
    assert q01["facts"][0]["value"] == "2026-11-02"
    q12 = next(item for item in data["questions"] if item["id"] == "Q-12")
    assert q12["facts"][0]["kind"] == "weekday"
    assert q12["facts"][0]["value"] == "понедельник"
    assert "{today" not in json.dumps(data, ensure_ascii=False)


def test_validator_rejects_unresolved_placeholder():
    """Неподставленный `{today_date}` — набор негоден: сверять не с чем."""
    errors = agent_eval.problems(load())
    assert any("подстановка" in error for error in errors), errors


def test_validator_catches_broken_set():
    data = load()
    data["questions"][0]["class"] = "chat"
    data["questions"][1]["text"] = data["questions"][0]["text"]
    errors = agent_eval.problems(data)
    assert any("класс" in error for error in errors)
    assert any("повтор вопроса" in error for error in errors)


def test_web_questions_expect_refusal():
    for item in load()["questions"]:
        if item["class"] == "web":
            assert item["expect"] == "refuse", item["id"]
            assert not item["facts"], item["id"]

"""Проверка механики замера A-0: сверка фактов, приговоры, таблица, агентный цикл.

Сеть не нужна: провайдер и инструменты подменяются. Проверяем то, что легко испортить
незаметно — разбор чисел и дат, различение «отказался» и «соврал», лимит шагов.
Замер и его метрики описаны в `docs/reports/112-agent-eval.md`.
"""

from __future__ import annotations

from datetime import date

import agent_eval
from agent_eval import Answer, Question, Summary

Q_DATE = Question(
    id="Q-11", text="какое сегодня число?", klass="tool", source="тест", why="тест",
    expect="answer", facts=[{"kind": "date", "value": "2026-11-01"}],
    forbidden=["2023", "2024", "2025"])
Q_WEB = Question(id="Q-27", text="какой курс доллара?", klass="web", source="тест",
                 why="тест", expect="refuse", facts=[])
Q_MONEY = Question(id="Q-19", text="сколько доходов в октябре?", klass="tool", source="тест",
                   why="тест", expect="answer",
                   facts=[{"kind": "money", "value": 210998},
                          {"kind": "phrase", "value": "октябр"}])
Q_CLARIFY = Question(id="Q-03", text="закупили всякое 500", klass="deterministic", source="тест",
                     why="тест", expect="answer", clarify=True,
                     facts=[{"kind": "money", "value": 500}])


# ---------------------------------------------------------------- числа и даты
def test_numbers_understand_thousand_separators():
    assert 210998 in agent_eval.numbers_in("доход 210 998 ₽")
    assert 45000 in agent_eval.numbers_in("оплатили 45 000")
    assert 700 in agent_eval.numbers_in("купили реквизит 700")


def test_numbers_do_not_glue_dates():
    found = agent_eval.numbers_in("28.10 с 10 до 14")
    assert 28 in found and 10 in found and 14 in found
    assert 2810 not in found


def test_trailing_sentence_dot_is_not_a_thousand_separator():
    """«заплатили 10,000.00.» — точка в конце предложения не делает из 10 000 миллион."""
    assert agent_eval.numbers_in("В октябре вы заплатили 10,000.00.") == {10000}
    assert agent_eval.numbers_in("Итого 210 998 ₽.") == {210998}


def test_date_fact_accepts_human_forms():
    assert agent_eval.fact_matches({"kind": "date", "value": "2026-11-01"},
                                   "Сегодня 1 ноября 2026 года.")
    assert agent_eval.fact_matches({"kind": "date", "value": "2026-11-01"}, "01.11.2026")
    assert agent_eval.fact_matches({"kind": "date", "value": "2026-11-01"}, "01.11.26")


def test_date_fact_rejects_other_year():
    assert not agent_eval.fact_matches({"kind": "date", "value": "2026-11-01"},
                                       "Сегодня 27 октября 2023 года")


def test_time_fact_forms():
    assert agent_eval.fact_matches({"kind": "time", "value": "15:00"}, "в 15:00 начнётся")
    assert not agent_eval.fact_matches({"kind": "time", "value": "15:00"}, "в 18:00 начнётся")
    # Ответ словами повторяет вопрос: «с 10 до 14» — это те же 10:00 и 14:00.
    assert agent_eval.fact_matches({"kind": "time", "value": "10:00"}, "с 10 до 14 свободен")
    assert not agent_eval.fact_matches({"kind": "time", "value": "10:00"}, "с 12 до 14 свободен")


# ---------------------------------------------------------------- приговоры
def test_correct_date_answer_is_adequate():
    verdict, _notes = agent_eval.score(Q_DATE, "Сегодня 1 ноября 2026 года, воскресенье.")
    assert verdict == "adequate"


def test_wrong_year_is_wrong():
    verdict, notes = agent_eval.score(Q_DATE, "Сегодня 27 октября 2023 года.")
    assert verdict == "wrong"
    assert notes


def test_honest_refusal_is_refused_not_wrong():
    verdict, _notes = agent_eval.score(Q_DATE, "Не могу посмотреть текущую дату: нет доступа.")
    assert verdict == "refused"


def test_web_question_refusal_is_adequate():
    verdict, _notes = agent_eval.score(Q_WEB, "Не могу посмотреть курс: доступа к интернету нет.")
    assert verdict == "adequate"


def test_web_question_number_without_tools_is_wrong():
    verdict, _notes = agent_eval.score(Q_WEB, "Доллар сегодня стоит 92 рубля.")
    assert verdict == "wrong"


def test_money_and_month_must_both_match():
    assert agent_eval.score(Q_MONEY, "В октябре доход 210 998 ₽.")[0] == "adequate"
    assert agent_eval.score(Q_MONEY, "В октябре доход 696 999 ₽.")[0] == "wrong"


def test_clarify_requires_a_question():
    assert agent_eval.score(Q_CLARIFY, "Записал 500 ₽.")[0] == "wrong"
    assert agent_eval.score(Q_CLARIFY, "500 ₽ — в какую категорию записать?")[0] == "adequate"


def test_all_facts_needed():
    question = Question(id="Q-14", text="что у меня сегодня?", klass="tool", source="тест",
                        why="тест", expect="answer",
                        facts=[{"kind": "phrase", "value": "Репа ЖН"},
                               {"kind": "time", "value": "15:00"}])
    assert agent_eval.score(question, "Сегодня 15:00, Репа ЖН.")[0] == "adequate"
    # Частичный ответ с честным пробелом — не выдумка, но и не ответ: «отказ».
    verdict, notes = agent_eval.score(question, "Сегодня Репа ЖН, время не знаю.")
    assert verdict == "refused"
    assert notes


# ---------------------------------------------------------------- метрики
def make_row(verdicts: list[str], model: str = "openai/gpt-4o-mini") -> Summary:
    answers = [Answer(question=f"Q-{index:02d}", verdict=verdict, notes=[], text="",
                      latency=1.0, steps=2, tokens_in=500, tokens_out=100)
               for index, verdict in enumerate(verdicts, start=1)]
    return Summary(model=model, mode="agent", answers=answers)


def test_shares_count_every_answer():
    row = make_row(["adequate", "refused", "wrong", "adequate"])
    assert row.count == 4
    assert row.share("adequate") == 0.5
    assert row.share("refused") == 0.25
    assert row.share("wrong") == 0.25


def test_table_has_model_by_metric_row():
    text = agent_eval.table([make_row(["adequate", "wrong"])])
    assert "модель" in text and "адекватно" in text and "p95" in text
    assert "openai/gpt-4o-mini" in text


def test_percentile_handles_edges():
    assert agent_eval.percentile([], 0.5) == 0.0
    assert agent_eval.percentile([2.0], 0.95) == 2.0
    assert agent_eval.percentile([1.0, 2.0, 3.0, 4.0], 0.5) in (2.0, 3.0)


def test_cost_per_1000_known_model():
    row = make_row(["adequate"])
    cost = row.cost_per_1000
    assert cost is not None and cost > 0


def test_cost_is_none_for_unknown_model():
    row = make_row(["adequate"], model="unknown/model")
    assert row.cost_per_1000 is None


# ---------------------------------------------------------------- цикл агента
def fake_chat_script(monkeypatch, completions):
    """Подменить провайдера: каждый вызов отдаёт следующую заготовку из списка."""
    calls: list[dict] = []

    def fake_chat(model, messages, tools=None, timeout=None):
        calls.append({"model": model, "messages": list(messages), "tools": tools})
        return completions[min(len(calls) - 1, len(completions) - 1)]

    monkeypatch.setattr(agent_eval, "chat", fake_chat)
    return calls


def test_agent_calls_tool_and_answers(monkeypatch):
    script = [
        agent_eval.Completion(message={"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-1", "type": "function",
             "function": {"name": "now", "arguments": "{}"}}]}, tokens_in=100, tokens_out=20),
        agent_eval.Completion(message={"role": "assistant",
                                       "content": "Сегодня 1 ноября 2026 года."},
                              tokens_in=200, tokens_out=30),
    ]
    calls = fake_chat_script(monkeypatch, script)
    monkeypatch.setattr(agent_eval, "call_tool", lambda name, args: {"ok": True, "date": "2026-11-01"})
    answer = agent_eval.run_agent("test/model", Q_DATE)
    assert answer.verdict == "adequate"
    assert answer.steps == 2
    assert answer.tools_used == ["now"]
    assert answer.tokens_in == 300
    assert calls[0]["tools"], "инструменты обязаны передаваться модели"


def test_agent_stops_at_step_limit(monkeypatch):
    loop = [agent_eval.Completion(message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "now", "arguments": "{}"}}]},
        tokens_in=10, tokens_out=5)]
    fake_chat_script(monkeypatch, loop)
    monkeypatch.setattr(agent_eval, "call_tool", lambda name, args: {"ok": True})
    answer = agent_eval.run_agent("test/model", Q_DATE, max_steps=3)
    assert answer.steps == 3
    assert answer.verdict == "wrong"


def test_single_mode_makes_one_call_without_tools(monkeypatch):
    calls = fake_chat_script(monkeypatch, [
        agent_eval.Completion(message={"role": "assistant",
                                       "content": "Не могу посмотреть дату."},
                              tokens_in=80, tokens_out=10)])
    answer = agent_eval.run_single("test/model", Q_DATE)
    assert answer.steps == 1
    assert answer.verdict == "refused"
    assert calls[0]["tools"] is None


def test_unknown_tool_does_not_crash():
    result = agent_eval.call_tool("no_such_tool", {})
    assert result["ok"] is False and result["error"] == "unknown_tool"


def test_web_search_is_disabled_by_default():
    result = agent_eval.call_tool("web_search", {"query": "курс"})
    assert result["ok"] is False
    assert result["error"] == "web_search_disabled"


def test_check_set_exits_zero(capsys):
    assert agent_eval.main(["--check-set"]) == 0
    assert "Набор годен" in capsys.readouterr().out


def test_rescore_rebuilds_verdicts_from_saved_run(tmp_path):
    """Пересчёт по JSON: ответы те же, приговоры считаются заново по набору."""
    import json

    row = Summary(model="openai/gpt-4o-mini", mode="agent", answers=[
        Answer(question="Q-01", verdict="refused", notes=[], text="Сегодня 1 ноября 2026 года.",
               latency=1.0, steps=2, tokens_in=100, tokens_out=10, why="дата"),
        Answer(question="Q-02", verdict="adequate", notes=[], text="Не могу посмотреть.",
               latency=1.0, steps=1, tokens_in=50, tokens_out=5, why="парсер"),
    ])
    path = tmp_path / "run.json"
    path.write_text(json.dumps(agent_eval.as_json([row], "2026-11-01"), ensure_ascii=False),
                    encoding="utf-8")
    # Дата прогона берётся из файла: Q-01 («какое сегодня число») сверяется с ней.
    set_path = agent_eval.resolve_today(agent_eval.load_set(), date(2026, 11, 1))
    rebuilt = agent_eval.from_json(path, set_path)
    assert len(rebuilt) == 1 and rebuilt[0].count == 2
    # Приговоры пересчитаны по фактам набора, а не взяты из файла.
    assert rebuilt[0].answers[0].verdict == "adequate"
    assert rebuilt[0].answers[1].verdict == "refused"
    assert rebuilt[0].answers[0].why.startswith("первый вопрос набора")


def test_broken_set_exits_one(tmp_path, capsys):
    broken = tmp_path / "set.json"
    broken.write_text('{"questions": []}', encoding="utf-8")
    assert agent_eval.main(["--check-set", "--set", str(broken)]) == 1
    assert "Набор негоден" in capsys.readouterr().err


def test_weekday_fact_matches_word_forms_and_catches_other_day():
    """День недели: «в воскресенье» — тот же факт, «понедельник» — другой день."""
    question = Question(id="Q-12", text="какой сегодня день недели?", klass="tool",
                        source="тест", why="тест", expect="answer",
                        facts=[{"kind": "weekday", "value": "воскресенье"}])
    assert agent_eval.score(question, "Сегодня воскресенье.")[0] == "adequate"
    assert agent_eval.score(question, "Свободно в воскресенье с 10 до 14")[0] == "adequate"
    verdict, notes = agent_eval.score(question, "Сегодня понедельник.")
    assert verdict == "wrong" and "другой день недели" in notes[0]

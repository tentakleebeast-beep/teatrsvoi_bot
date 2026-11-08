"""Стоимость 1000 сообщений: счёт по замеру, а не оценка (карта A-4).

Держим три вещи:
  * формула: $/сообщение = (вход × цена_вход + выход × цена_выход) / 1M, ₽ = $ × курс;
  * цифры отчёта `docs/reports/113` воспроизводятся из сохранённого прогона A-0
    (`--check`), а не переписаны руками в текст;
  * режим «классификатор + исполнитель» складывается из двух измеренных частей,
    и его нельзя посчитать, если одна из них не замерена.

Сети нет: прогон и цены — файлы в репозитории.
"""

from __future__ import annotations

import json
import pathlib

import agent_cost
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "docs" / "reports" / "112-agent-eval-run-01.11.2026.json"
EXPECTED = ROOT / "docs" / "reports" / "113-agent-cost.json"
CLASSIFIER = ROOT / "docs" / "reports" / "113-classifier-run-04.11.2026.json"
CONFIG = ROOT / "config" / "agent-models.yaml"


@pytest.fixture(autouse=True)
def _config(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODELS_PATH", str(CONFIG))


def test_цена_сообщения_по_токенам() -> None:
    # 1M входных токенов по $0,15 и 1M выходных по $0,60 — это ровно $0,75.
    assert agent_cost.message_cost_usd(1_000_000, 0, (0.15, 0.60)) == pytest.approx(0.15)
    assert agent_cost.message_cost_usd(0, 1_000_000, (0.15, 0.60)) == pytest.approx(0.60)
    assert agent_cost.message_cost_usd(1000, 40, (0.15, 0.60)) == pytest.approx(0.000174)


def test_1000_сообщений_и_курс() -> None:
    money = agent_cost.per_1000(1043.9, 40.8, (0.15, 0.60), 80.5573)
    assert money["usd_per_1000"] == pytest.approx(0.181065, abs=1e-6)
    assert money["rub_per_1000"] == pytest.approx(14.59, abs=0.01)


def test_нет_цены_модели_это_ошибка_а_не_ноль() -> None:
    with pytest.raises(ValueError):
        agent_cost._price("нет/такой-модели", {"openai/gpt-4o-mini": (0.15, 0.60)})


def test_средние_по_всем_ответам_прогона() -> None:
    run = {"model": "openai/gpt-4o-mini", "mode": "agent",
           "answers": [{"tokens_in": 100, "tokens_out": 10},
                       {"tokens_in": 300, "tokens_out": 30}]}
    mean = agent_cost.run_mean(run)
    assert mean == {"count": 2, "tokens_in": 200.0, "tokens_out": 20.0}


def test_пустой_прогон_не_считается() -> None:
    with pytest.raises(ValueError):
        agent_cost.run_mean({"model": "m", "mode": "agent", "answers": []})


def test_цифры_отчёта_воспроизводятся_из_замера() -> None:
    """Главная проверка карты: ₽/1000 в отчёте — из этого прогона, а не из головы."""
    result = agent_cost.build(RUN, None)
    rows = {(row["model"], row["mode"]): row for row in result["rows"]}

    assert len(rows) == 6
    assert rows[("openai/gpt-4o-mini", "agent")]["usd_per_1000"] == pytest.approx(
        0.1811, abs=1e-3)
    assert rows[("openai/gpt-4o-mini", "agent")]["rub_per_1000"] == pytest.approx(
        14.59, abs=0.01)
    assert rows[("openai/gpt-4.1-nano", "agent")]["rub_per_1000"] == pytest.approx(
        10.16, abs=0.01)
    assert rows[("openai/gpt-4o", "agent")]["rub_per_1000"] == pytest.approx(
        242.92, abs=0.01)
    assert rows[("openai/gpt-4o-mini", "single")]["rub_per_1000"] == pytest.approx(
        2.38, abs=0.01)


def test_режим_агента_дороже_одиночного_вызова() -> None:
    """Это и есть цена инструментов: цифра должна быть видна, а не обсуждаема."""
    result = agent_cost.build(RUN, None)
    rows = {(row["model"], row["mode"]): row for row in result["rows"]}
    for model in ("openai/gpt-4.1-nano", "openai/gpt-4o-mini", "openai/gpt-4o"):
        assert (rows[(model, "agent")]["rub_per_1000"]
                > rows[(model, "single")]["rub_per_1000"])


def test_курс_и_источник_попадают_в_расчёт() -> None:
    result = agent_cost.build(RUN, None)
    assert result["usd_rub"] > 0
    assert result["usd_rub_source"], "без источника курс — оценка"
    assert result["usd_rub_date"] == "2026-11-02"


def test_классификатор_складывается_с_исполнителем() -> None:
    classifier = {"model": "openai/gpt-4.1-nano", "mode": "classifier",
                  "measured": [{"tokens_in": 110, "tokens_out": 1, "latency": 0.4},
                               {"tokens_in": 130, "tokens_out": 1, "latency": 0.6}]}
    doc = agent_cost.analyse_classifier(classifier, {"openai/gpt-4.1-nano": (0.10, 0.40)},
                                        80.5573)
    assert doc["tokens_in"] == pytest.approx(120.0)
    assert doc["tokens_out"] == pytest.approx(1.0)

    base = [{"model": "openai/gpt-4o-mini", "mode": "agent", "count": 30,
             "tokens_in": 1043.9, "tokens_out": 40.8, "usd_per_1000": 0.1811,
             "rub_per_1000": 14.59, "adequate": 0.57, "p95": 5.5}]
    combined = agent_cost.combine(base, doc)

    assert len(combined) == 1
    row = combined[0]
    assert row["mode"] == "assistant"
    assert row["tokens_in"] == pytest.approx(1163.9)
    assert row["rub_per_1000"] > 14.59


def test_таблица_содержит_рубли_и_доллары() -> None:
    rows = [{"model": "openai/gpt-4o-mini", "mode": "agent", "tokens_in": 1043.9,
             "tokens_out": 40.8, "usd_per_1000": 0.1811, "rub_per_1000": 14.59}]
    text = agent_cost.table(rows)
    assert "₽/1000" in text and "$/1000" in text
    assert "| openai/gpt-4o-mini | agent |" in text
    assert "14.59" in text


def test_check_ловит_расхождение_с_отчётом(tmp_path, capsys) -> None:
    result = agent_cost.build(RUN, None)
    wrong = {"rows": [dict(result["rows"][0], rub_per_1000=999.0)]}
    path = tmp_path / "expected.json"
    path.write_text(json.dumps(wrong), encoding="utf-8")

    assert agent_cost.check(result, path) == 1
    assert "РАСХОЖДЕНИЕ" in capsys.readouterr().err


def test_check_проходит_на_сохранённых_цифрах() -> None:
    """Отчёт и 113-agent-cost.json сходятся вместе с замером классификатора."""
    result = agent_cost.build(RUN, CLASSIFIER)
    assert agent_cost.check(result, EXPECTED) == 0
    rows = {(row["model"], row["mode"]): row for row in result["rows"]}
    assert ("openai/gpt-4o-mini", "assistant") in rows
    assert rows[("openai/gpt-4o-mini", "assistant")]["rub_per_1000"] == pytest.approx(
        15.44, abs=0.01)


def test_нет_файла_ожиданий_это_ошибка(tmp_path) -> None:
    result = agent_cost.build(RUN, None)
    assert agent_cost.check(result, tmp_path / "нет.json") == 1

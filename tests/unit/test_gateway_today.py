"""Вопрос про сегодняшнюю дату отвечает календарь проекта, а не модель.

Дефект 01.11.2026: на «какой сегодня день» бот ответил «27 октября 2023 года» —
запрос уходил в LLM без текущей даты в промпте, и модель придумывала.

Правила и требования раздела: docs/development/21-intent-cascade.md.
"""

from __future__ import annotations

import datetime

import gateway_modules
import pytest


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_стандартные_формулировки_распознаются(gw):
    for text in ("какой сегодня день", "Какое сегодня число?",
                 "какая сегодня дата", "Какой день недели", "сегодня какое число",
                 "а какой сегодня день"):
        assert gw.asks_today(text), text


def test_обычные_фразы_не_перехватываются(gw):
    for text in ("какой день лучше для репетиции",
                 "сколько у нас денег",
                 "поставь репетицию на завтра",
                 "какой сегодня день лучше выбрать для прогона"):
        assert not gw.asks_today(text), text


def test_ответ_содержит_настоящую_дату_и_день_недели(gw):
    answer = gw.today_answer(datetime.date(2026, 11, 1))
    assert answer == "Сегодня воскресенье, 1 ноября 2026 года."
    assert "2023" not in answer

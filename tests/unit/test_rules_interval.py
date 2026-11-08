"""Разбор интервала часов без даты — исправление инцидента 28.10.2026.

Причина: форма занятости уже знает день, но звала общий разбор, которому нужна дата
в тексте. «с 10 до 14» возвращало need_date, бот повторял «не разобрал время» бесконечно.
Обоснование (Ш-51): файл создан в b35986c fix(gateway,api): форма занятости не зацикливается и всегда выпускает человека (INT-004).
"""

import pytest
from app.rules import parse_interval


@pytest.mark.parametrize("text,start,end", [
    ("с 10 до 14", 10, 14),
    ("10 до 14", 10, 14),
    ("С 10 ДО 14", 10, 14),
    ("10-14", 10, 14),
    ("10 — 14", 10, 14),
    ("с 10:00 до 14:00", 10, 14),
    ("с 10 до 14 часов", 10, 14),
    ("свободен с 18 до 23", 18, 23),
    ("занят с 9 по 12", 9, 12),
])
def test_интервал_разбирается(text, start, end):
    res = parse_interval(text)
    assert res["ok"] is True, res
    assert (res["start"], res["end"]) == (start, end)


@pytest.mark.parametrize("text", ["", "не понял", "10", "с до", "14 до 10", "25 до 26"])
def test_мусор_и_перевёрнутый_интервал_отклоняются(text):
    assert parse_interval(text)["ok"] is False


def test_ночной_интервал_помечен_как_вне_рабочего_дня():
    res = parse_interval("с 2 до 5")
    assert res["ok"] and res["outside_work_day"] is True
    assert "вне рабочего дня" in res["label"]


def test_рабочий_день_без_пометки():
    assert parse_interval("с 10 до 14")["outside_work_day"] is False


def test_до_24_показывается_как_23():
    res = parse_interval("с 18 до 24")
    assert res["ok"] and res["end"] == 24
    assert "18:00–23:00" in res["label"]

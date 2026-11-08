"""Разбор трат: docs/development/16 §4.3 (эталонные фразы)."""

from datetime import date

import pytest
from app.rules import detect_amount, detect_category, detect_type, parse_budget_text

TODAY = date(2026, 10, 26)


@pytest.mark.parametrize("text,expected", [
    ("расход 3000 на реквизит", 3000),
    ("купили ткань на костюмы 12к налом", 12000),
    ("оплатили аренду 45 000 за октябрь", 45000),
    ("перевели 350000 за ремонт помещения", 350000),
    ("кофе 1,5к", 1500),
    ("созвонимся в 19", 19),
])
def test_сумма_распознаётся(text, expected):
    assert detect_amount(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("расход 3000 на реквизит", "expense"),
    ("купили ткань 12к", "expense"),
    ("оплатили аренду 45к", "expense"),
    ("сдали взнос 5000", "income"),
    ("приход от показа 30000", "income"),
    ("созвонимся завтра", None),
])
def test_тип_операции(text, expected):
    assert detect_type(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("расход 3000 на реквизит", "Реквизит и костюмы"),
    ("оплатили аренду 45к", "Аренда помещения"),
    ("комуналка 8700", "Коммунальные платежи"),
    ("21% Крылову", "Гонорары и доли"),
    ("программки и афиша 5052", "Реклама и полиграфия"),
    ("съемка Юля 5000", "Фото/видео"),
    ("тренинг 50000", "Тренинги и занятия"),
    ("что-то непонятное 100", None),
])
def test_категория(text, expected):
    assert detect_category(text) == expected


def test_полная_фраза_разбирается_целиком():
    res = parse_budget_text("оплатили аренду 45 000 за октябрь", today=TODAY)
    assert res["ok"] is True
    assert res["flags"] == []
    d = res["draft"]
    assert d["amount"] == 45000 and d["tx_type"] == "expense"
    assert d["category"] == "Аренда помещения"
    assert d["tx_date"] == "2026-10-26"


def test_без_суммы_флаг_amount_missing():
    res = parse_budget_text("купили ткань на костюмы", today=TODAY)
    assert res["ok"] is False
    assert "amount_missing" in res["flags"]


def test_без_типа_флаг_type_missing():
    res = parse_budget_text("3000 реквизит", today=TODAY)
    assert res["ok"] is False
    assert "type_missing" in res["flags"]
    assert res["draft"]["amount"] == 3000


def test_не_трата_не_считается_операцией():
    res = parse_budget_text("созвонимся завтра в 19", today=TODAY)
    assert res["ok"] is False
    assert "type_missing" in res["flags"]


def test_проект_и_контрагент():
    res = parse_budget_text("расход 5000 на Бунин Зайцева", today=TODAY)
    assert res["draft"]["project"] == "Бунин"
    assert res["draft"]["counterparty"] == "Зайцева"

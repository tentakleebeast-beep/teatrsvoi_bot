"""Свободный текст → карточка операции (docs/development/82 §4).

Требование заказчика: если в тексте есть данные, бот сразу собирает операцию и
показывает её на подтверждение, а не спрашивает «хотите сообщить доход?».

Гейтвей целиком не импортируется (aiogram и окружение), поэтому блок разбора
берём из исходника — так же, как другие тесты гейтвея.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from datetime import date, datetime, timedelta, timezone

import gateway_modules
import pytest

GATEWAY = (pathlib.Path(__file__).resolve().parents[2] / "services" / "tg-gateway"
           / "app" / "parsers.py")


def _gateway_settings():
    """Настройки гейтвея для исполняемого блока (R-4: окружение читает только settings.py)."""
    path = GATEWAY.parent / "settings.py"
    name = "gw_settings_for_text_to_draft"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # dataclass со строковыми аннотациями ищет модуль здесь
    spec.loader.exec_module(module)
    return module.Settings.from_env({})


@pytest.fixture(scope="module")
def gw() -> dict:
    source = GATEWAY.read_text(encoding="utf-8")
    start = source.index("# --- свободный текст")
    end = source.index("# --- файлы")
    import datetime as _module
    import os

    # В гейтвее `datetime` — модуль (import datetime), а `_dt`/`_date` — классы.
    # `settings` — объект настроек гейтвея (R-4); блок берёт из него LOCAL_TZ.
    namespace: dict = {
        "re": re, "os": os, "date": date, "datetime": _module,
        "_date": date, "_dt": datetime,
        "settings": _gateway_settings(),
        "LOCAL_TZ": _gateway_settings().local_tz,
        # карточка живёт в texts.py: блок разбора её только вызывает
        "draft_card": gateway_modules.load("texts").draft_card,
        "texts": gateway_modules.load("texts"),
        "money": lambda value: f"{float(value):,.0f}".replace(",", " ") + " ₽",
    }
    exec(source[start:end], namespace)   # noqa: S102 — разбор тестового блока
    return namespace


CASES = [
    ("получила 15000 от Валерия за сентябрь на аренду",
     {"tx_type": "income", "amount": 15000.0, "category": "Субаренда",
      "tx_date": "2026-09-01", "purpose": "Валерия"}),
    ("оплатил аренду 45 000", {"tx_type": "expense", "amount": 45000.0,
                               "category": "Аренда помещения"}),
    ("потратил 2,5 тыс на рекламу", {"tx_type": "expense", "amount": 2500.0,
                                     "category": "Реклама и полиграфия"}),
    ("взнос 5000 от Марии", {"tx_type": "income", "amount": 5000.0,
                             "category": "Взносы студийцев"}),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_текст_превращается_в_операцию(gw, text, expected):
    draft = gw["text_to_draft"](text)
    assert draft is not None, f"не разобрал: {text}"
    for key, value in expected.items():
        assert draft[key] == value, f"{text}: {key} = {draft[key]!r}, ждали {value!r}"


@pytest.mark.parametrize("text", ["привет, как дела?", "покажи расписание на неделю",
                                  "кто я", "сколько денег осталось?",
                                  # интервал часов — это время, а не сумма (t_ebce081e)
                                  "с 15-00 до 18-00", "15-18", "с 15 до 18",
                                  "в 19:00", "завтра с 10 до 14"])
def test_без_суммы_черновика_нет(gw, text):
    assert gw["text_to_draft"](text) is None


@pytest.mark.parametrize("text", ["3000", "3000 на реквизит", "получили 8000 за билеты"])
def test_сумма_без_знака_валюты_остаётся_операцией(gw, text):
    """docs/82 §4.1: фраза с суммой — уже операция; денежный знак не обязателен."""
    assert gw["text_to_draft"](text) is not None


def test_деньги_рядом_с_временем_не_теряются(gw):
    """«потратил 15 000» — операция, даже если рядом написано время."""
    draft = gw["text_to_draft"]("потратил 15 000 за смену с 15 до 18")
    assert draft is not None and draft["amount"] == 15000.0


def test_карточка_называет_детали(gw):
    draft = gw["text_to_draft"]("получила 15000 от Валерия за сентябрь на аренду")
    card = gw["draft_card"](draft)
    assert "Записать доход?" in card
    assert "Субаренда" in card        # доход за помещение — субаренда, не «Аренда»
    assert "15 000" in card
    assert "01.09.2026" in card
    assert "Валерия" in card        # имя остаётся как в тексте, падеж не выправляем

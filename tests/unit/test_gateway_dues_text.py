"""
FR-B7, часть 2: «Мои взносы» — коротко: тариф и учтено ли за текущий месяц.

Решение 29.10.2026: реестр начислений по месяцам человеку в чате не нужен, он
живёт в учёте у организатора. Человеку важен ответ «за этот месяц всё» и долг,
если он есть. Цифры считает сервер и присылает готовыми (`current`, `debt`).
"""

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_действующий_тариф_и_учтённый_месяц(gw):
    res = {"tariff": {"tariff": 1200, "tariff_from": "2026-01-01", "amount": 1200},
           "current": {"period": "2026-10", "due": 1200, "paid": 1200,
                       "settled": True, "charged": True},
           "debt": 0}

    lines = gw.dues_text(res).splitlines()

    assert lines[0] == "Тариф: 1 200 ₽ в месяц, с 2026-01-01"
    assert lines[1] == "За октябрь 2026 учтено: 1 200 ₽."
    assert len(lines) == 2, "реестр по месяцам в чат больше не выводится"


def test_неполная_оплата_показывает_остаток(gw):
    res = {"tariff": {"tariff": 1200, "amount": 1200},
           "current": {"period": "2026-10", "due": 1200, "paid": 500,
                       "settled": False, "charged": True},
           "debt": 700}

    text = gw.dues_text(res)

    assert "За октябрь 2026 не учтено: внесено 500 ₽ из 1 200 ₽, осталось 700 ₽." in text
    assert "Долг за прошлые месяцы: 700 ₽." in text


def test_тариф_со_следующего_месяца_показан_с_датой(gw):
    res = {"tariff": {"tariff": 2000, "tariff_from": "2026-11-01", "amount": None},
           "current": {"charged": False}, "debt": 0}

    text = gw.dues_text(res)

    assert "Тариф: 2 000 ₽ в месяц, с 2026-11-01" in text
    assert "За текущий месяц начислений нет." in text


def test_без_тарифа(gw):
    res = {"tariff": {"tariff": None, "tariff_from": None, "amount": None},
           "current": {"charged": False}, "debt": 0}

    assert gw.dues_text(res).splitlines() == [
        "Тариф не назначен: сумму определяет организатор.",
        "За текущий месяц начислений нет."]


def test_чат_печатает_строки_сервера_без_своей_копии(gw):
    """Д12: один текст на чат и экран — строки приходят в `summary` и печатаются как есть.

    Строки строит `services/shared/dues.py`; экран Mini App берёт те же `summary.lines`.
    Если бот начнёт собирать свой текст, месяц и числа снова разойдутся (4.7 отчёта 122).
    """
    res = {"tariff": {"tariff": 1200, "amount": 1200},
           "current": {"period": "2026-11", "due": 1200, "paid": 500,
                       "settled": False, "charged": True},
           "debt": 700,
           "summary": {"period": "2026-11", "title": "ноябрь 2026",
                       "lines": ["Тариф: 1 200 ₽ в месяц",
                                 "За ноябрь 2026 не учтено: внесено 500 ₽ из 1 200 ₽, "
                                 "осталось 700 ₽.",
                                 "Долг за прошлые месяцы: 700 ₽."]}}

    assert gw.dues_text(res) == "\n".join(res["summary"]["lines"])

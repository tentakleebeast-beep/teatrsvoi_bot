"""Каскад разбора: правила → модель из закрытого списка → кнопки (docs/development/21)."""

from datetime import date

from app.rules import CONFIDENCE_THRESHOLD, cascade_decide

ALLOWED = ["Аренда помещения", "Реквизит и костюмы", "Коммунальные платежи",
           "Гонорары и доли", "Фото/видео", "Прочее"]
TODAY = date(2026, 10, 26)


def test_порог_равен_07():
    assert CONFIDENCE_THRESHOLD == 0.7


def test_правила_побеждают_модель():
    d = cascade_decide("оплатили аренду 45 000", ALLOWED,
                       llm_category="Реквизит и костюмы", llm_confidence=0.99, today=TODAY)
    assert d["source"] == "rules"
    assert d["category_final"] == "Аренда помещения"
    assert d["need"] is None


def test_модель_выбирает_из_списка_при_высокой_уверенности():
    d = cascade_decide("закупили всякое 500", ALLOWED,
                       llm_category="Реквизит и костюмы", llm_confidence=0.9, today=TODAY)
    assert d["source"] == "llm"
    assert d["category_final"] == "Реквизит и костюмы"


def test_низкая_уверенность_ведёт_к_кнопкам():
    d = cascade_decide("закупили всякое 500", ALLOWED,
                       llm_category="Реквизит и костюмы", llm_confidence=0.65, today=TODAY)
    assert d["source"] is None
    assert d["need"] == "buttons"
    assert d["candidates"]


def test_категория_вне_списка_не_принимается():
    d = cascade_decide("закупили всякое 500", ALLOWED,
                       llm_category="Реквизит и декорации", llm_confidence=0.99, today=TODAY)
    assert d["source"] is None
    assert d["need"] == "buttons"


def test_прочее_от_модели_не_считается_ответом():
    d = cascade_decide("закупили всякое 500", ALLOWED,
                       llm_category="Прочее", llm_confidence=0.99, today=TODAY)
    assert d["source"] is None
    assert d["need"] == "buttons"


def test_без_ответа_модели_просим_модель():
    d = cascade_decide("закупили всякое 500", ALLOWED, today=TODAY)
    assert d["need"] == "llm"
    assert d["category_final"] is None


def test_нет_суммы_решение_не_принимается():
    d = cascade_decide("купили ткань", ALLOWED, llm_category="Реквизит и костюмы",
                       llm_confidence=0.95, today=TODAY)
    assert d["ok"] is False
    assert d["category_final"] == "Реквизит и костюмы"   # категория есть, суммы нет
    assert "amount_missing" in d["flags"]

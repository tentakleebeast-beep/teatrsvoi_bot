"""Цена ответа агента в рублях — по конфигу и по факту, а не нулём (AUD-10).

Зачем модуль. До карты AUD-10 (`t_4a9705ea`) `llm_usage` писала `tokens_in = 0`,
`tokens_out = все токены`, `cost_rub = 0`: стоимость агентного режима в бою не
считалась вовсе, и единственная цифра «сколько это стоит» была замером из
`docs/reports/113`, а не боевыми данными (`docs/reports/119` §9 п.5, §10 п.5).

Что считается:

    ₽ = (tokens_in × цена_входа + tokens_out × цена_выхода) / 1 000 000 × курс

Цены (`prices_usd_per_million`) и курс (`currency.usd_rub`) берутся из
`config/agent-models.yaml` через `agently/models.py` — того же файла, по которому
считает `scripts/agent_cost.py`. Второго источника цифр нет: если провайдер поднял
цену, правится один файл, и журнал с отчётом не разойдутся.

Неизвестная модель — это не «бесплатно», а «цены нет». Поэтому у неизвестной модели
`cost_rub` остаётся `None` (в `llm_usage` — NULL), а рядом лежит
`price_status = 'no_price'`. Ноль в `cost_rub` значил бы «посчитано и вышло даром»,
и сумма расходов молча занижалась бы — ровно та ошибка, из-за которой цифру брали
из замера.

Тест: `tests/unit/test_agent_budget.py` (сверка с ручным расчётом по конфигу).
"""

from __future__ import annotations

from . import models

#: Признаки цены в `llm_usage.price_status` — закрытый список (миграция 055).
PRICE_KNOWN = "known"
PRICE_UNKNOWN = "no_price"

#: Знаков после запятой в `llm_usage.cost_rub` (numeric(10,4)).
DIGITS = 4


def price(model: str | None) -> tuple[float, float] | None:
    """Цена модели, $ за 1M токенов: (вход, выход). None — модель в конфиге не описана."""
    if not model:
        return None
    return models.prices().get(str(model))


def rate() -> float:
    """Курс доллара для колонки «рубли» (тот же, что в отчёте о стоимости)."""
    return float(models.rates()["usd_rub"])


def cost_rub(model: str | None, tokens_in: int, tokens_out: int) -> float | None:
    """Стоимость вызова в рублях. None — цены модели нет (не ноль!)."""
    value = price(model)
    if value is None:
        return None
    usd = (int(tokens_in or 0) * value[0] + int(tokens_out or 0) * value[1]) / 1_000_000
    return round(usd * rate(), DIGITS)


def row(model: str | None, tokens_in: int, tokens_out: int) -> dict:
    """Строка учёта для `llm_usage`: рубли и честный признак «цена известна»."""
    rub = cost_rub(model, tokens_in, tokens_out)
    return {
        "model": model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_rub": rub,
        "price_status": PRICE_UNKNOWN if rub is None else PRICE_KNOWN,
        "usd_rub": rate(),
    }

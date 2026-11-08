"""Ввод суммы в Mini App: «1 000», «500,50», «500 ₽» — это числа.

Обоснование (Ш-51): на проде 30.10.2026 запись дохода падала с «не записано
[object Object]». Было две причины: страница подставляла список ошибок валидации
в строку, а сумма с запятой или пробелом между разрядами отвергалась как не-число.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

CASES = [
    ("1200", 1200.0),
    ("1 000", 1000.0),
    ("500,50", 500.5),
    ("1\u00a0500", 1500.0),          # неразрывный пробел из iOS
    ("500 ₽", 500.0),
    (300, 300.0),
    (99.99, 99.99),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_сумма_разбирается_как_число(raw, expected):
    from app.api.budget import BudgetCommitIn

    assert BudgetCommitIn(amount=raw).amount == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "пять тысяч"])
def test_нечисло_по_прежнему_ошибка(raw):
    from app.api.budget import BudgetCommitIn

    with pytest.raises(ValidationError):
        BudgetCommitIn(amount=raw)


def test_пустая_категория_становится_прочим():
    from app.api.budget import BudgetCommitIn

    assert BudgetCommitIn(amount=100, category="").category == "Прочее"
    assert BudgetCommitIn(amount=100, category="   ").category == "Прочее"
    assert BudgetCommitIn(amount=100, category=None).category == "Прочее"
    assert BudgetCommitIn(amount=100, category="Аренда").category == "Аренда"


def test_статья_необязательна():
    """Страница присылала null в необязательное поле — 422 «Input should be a valid string»."""
    from app.api.budget import BudgetCommitIn

    assert BudgetCommitIn(amount=100, purpose=None).purpose == ""
    assert BudgetCommitIn(amount=100).purpose == ""
    assert BudgetCommitIn(amount=100, purpose="Бунин").purpose == "Бунин"

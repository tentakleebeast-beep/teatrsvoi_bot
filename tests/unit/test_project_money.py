"""Деньги проекта без базы: блок по статье, алиасы категории, поля операции.

Три решения, от которых зависит остальное: доход со статьёй «Аренда (доход)»
попадает в `sublease`, старое имя «Субаренда» читается как алиас, а поля
производной операции выводятся из типа события — аренда или показ арендатора.

Обоснование: docs/development/196 §7.1–7.3, §9; AC-16, AC-17, AC-26, AC-31.
"""

from __future__ import annotations

from datetime import date

import pytest
from app import category_alias, project_money
from app.api.budget import block_for


@pytest.mark.parametrize("tx_type,category,expected", [
    ("income", "Аренда (доход)", "sublease"),
    ("income", "Субаренда", "sublease"),
    ("income", "Продажа билетов", "show_income"),
    ("income", "Курсы", "show_income"),
    ("expense", "Аренда помещения", "expense"),
    ("expense", "Аренда (доход)", "expense"),
    ("transfer", None, "expense"),
])
def test_блок_выводится_из_статьи(tx_type, category, expected):
    """R-5: доход за зал — отдельный блок, а не «Показы»."""
    assert block_for(tx_type, category) == expected


def test_старое_имя_статьи_читается_как_алиас():
    """AC-31: «Субаренда» — алиас «Аренды (доход)», второй статьи не появляется."""
    assert category_alias.canonical("Субаренда") == "Аренда (доход)"
    assert category_alias.canonical("  аренда (ДОХОД) ") == "Аренда (доход)"
    assert category_alias.is_lease_income("субаренда") is True
    assert category_alias.is_lease_income("Аренда помещения") is False


def test_незнакомое_имя_не_трогается():
    """Канонизация не переписывает прочие статьи: имя в базе остаётся как есть."""
    assert category_alias.canonical("Гонорары и доли") == "Гонорары и доли"


def test_аренда_даёт_субаренду_и_статью_аренды():
    """AC-16: тип события `rental` — блок `sublease`, статья «Аренда (доход)»."""
    fields = project_money.derived_fields("rental", "Валерий", date(2026, 11, 26))
    assert fields["block"] == "sublease"
    assert fields["category"] == "Аренда (доход)"
    assert fields["purpose"] == "Аренда — Валерий — 26.11.2026"


def test_показ_арендатора_даёт_показы():
    """AC-17: тип события `show` — блок `show_income`, статья «Продажа билетов»."""
    fields = project_money.derived_fields("show", "Валерий", date(2026, 11, 27))
    assert fields["block"] == "show_income"
    assert fields["category"] == "Продажа билетов"
    assert fields["purpose"] == "Показ — Валерий — 27.11.2026"

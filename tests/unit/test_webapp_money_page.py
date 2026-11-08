"""«Деньги» и форма операции: два экрана на дизайн-системе (docs/92 §2.3, docs/93 §4).

Проверяем то, из-за чего переделывались деньги: таблица вместо строк, мелкие цели,
эмодзи, `alert`/`confirm` и дата в виде `2026-10-31`.

Правила и требования раздела: docs/development/91-design-system.md.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import money  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


def test_деньги_это_вкладка_а_операция_вложенный_экран():
    assert money.MONEY_PAGE.startswith("<!doctype html>")
    assert 'class="ui-screen ui-screen--tabbed"' in money.MONEY_PAGE
    assert "ui-tabbar" in money.MONEY_PAGE and "ui-appbar" not in money.MONEY_PAGE

    assert money.TX_PAGE.rstrip().endswith("</html>")
    assert 'class="ui-appbar__back" href="/webapp/money"' in money.TX_PAGE
    assert "ui-tabbar" not in money.TX_PAGE
    # «Назад» из формы ведёт в «Деньги», а не в «Главную» (docs/92 §6).
    assert 'window.SVOI_BACK = "/webapp/money"' in money.TX_PAGE


def test_три_сегмента_денег_и_одно_главное_действие():
    for label in ("Операции", "Бюджет", "Мои взносы"):
        assert label in money.MONEY_PAGE
    assert 'href="/webapp/tx">Записать операцию' in money.MONEY_PAGE
    # Права прячут сегменты, а не показывают недоступное: по умолчанию видны только взносы.
    assert 'id="money-seg"' in money.MONEY_PAGE and "hidden" in money.MONEY_PAGE
    assert money.MONEY_PAGE.count("ui-btn--primary") == 1


def test_операция_одна_кнопка_и_дата_компонентом():
    assert money.TX_PAGE.count("ui-btn--primary") == 1
    assert ">Записать<" in money.TX_PAGE
    # Поле суммы: 20 px и десятичная клавиатура — иначе iOS приближает страницу.
    assert 'inputmode="decimal"' in money.TX_PAGE
    assert "ui-input--amount" in money.TX_PAGE
    # Дата: человеческая подпись + нативный пикер внутри контрола + чипы.
    assert 'class="ui-datefield"' in money.TX_PAGE
    assert 'input type="date"' in money.TX_PAGE
    for chip in ("Сегодня", "Вчера", "Завтра"):
        assert chip in money.TX_PAGE
    # План/факт — сегмент, а не чекбокс.
    assert 'data-plan="plan"' in money.TX_PAGE and 'data-plan="fact"' in money.TX_PAGE


def test_никаких_таблиц_эмодзи_и_модальных_окон():
    for html in (money.MONEY_PAGE, money.TX_PAGE):
        assert "<table" not in html
        assert not EMOJI.search(html)
        assert "alert(" not in html
        assert "confirm(" not in html
        assert "prompt(" not in html
        assert "overflow-x: auto" not in html


def test_результат_и_ошибки_через_оболочку():
    # Запись видна и отменяема: сообщение с «Вернуть», а не строка «Статус: записано».
    assert "SVOI.toast" in money.TX_PAGE
    assert '"Вернуть"' in money.TX_PAGE
    assert "SVOI.sheet" in money.TX_PAGE
    # Ошибка FastAPI раскладывается по полям и показывается под полем.
    for field in ("amount", "category", "tx_date", "purpose"):
        assert f'id="err-{field}"' in money.TX_PAGE
    assert "err-" in money.TX_PAGE and "ui-field__error" in money.TX_PAGE


def test_суммы_и_даты_по_русски_на_странице():
    # Форматирование повторяет layout.money: разряды неразрывным пробелом, знак ₽.
    assert "\\u00A0" in money.MONEY_PAGE or "\u00a0" in money.MONEY_PAGE
    assert "\\u2212" in money.TX_PAGE or "\u2212" in money.TX_PAGE
    for month in ("января", "октября", "декабря"):
        assert month in money.MONEY_PAGE

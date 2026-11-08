"""
Справка не отстаёт от меню: каждый пункт описан, лишних строк нет.

Раньше список «Что умею» был отдельным текстом и упоминал команды, которых в
меню уже не было. Теперь справка строится из MENU_GROUPS, а этот тест требует,
чтобы у каждого пункта была одна честная строка описания.

Правила и требования раздела: docs/development/82-ux-navigation.md.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "tg-gateway"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_каждый_пункт_меню_описан(gw) -> None:
    missing = [item for item in gw.MENU_ACTIONS if not gw.MENU_HELP.get(item)]
    assert missing == [], f"нет описания для пунктов: {missing}"


def test_нет_описаний_без_пунктов(gw) -> None:
    extra = [item for item in gw.MENU_HELP if item not in gw.MENU_ACTIONS]
    assert extra == [], f"описаны несуществующие пункты: {extra}"


def test_справка_перечисляет_все_доступные_пункты(gw) -> None:
    caps = ["schedule.availability.self", "budget.read.self", "feedback.send"]
    text = gw.help_text(caps)
    for group, items in gw.menu_groups(caps):
        assert group in text, f"нет раздела {group}"
        for item in items:
            assert f"· {item} — {gw.MENU_HELP[item]}" in text, f"нет строки про {item}"


def test_справка_не_обещает_недоступное(gw) -> None:
    """Труппа не видит админ-панель и расписание — и справка о них не говорит."""
    text = gw.help_text(["budget.read.self"])
    assert "Админ-панель" not in text
    assert "Расписание" not in text
    assert "Мои взносы" in text


def test_в_справке_нет_команд_для_ручного_ввода(gw) -> None:
    """Правило заказчика: админский доступ — в панели, участникам — кнопки и текст."""
    text = gw.help_text(["admin"])
    assert "/" not in text.replace("08:00", "").replace("24:00", ""), text

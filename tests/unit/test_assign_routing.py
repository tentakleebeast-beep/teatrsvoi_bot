"""Фраза «назначь …» уходит в назначение события, а не в поиск окон (docs/architecture/51)."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "services" / "tg-gateway" / "app"))

from routing import ASSIGN_Q, RULES, SLOTS_Q  # noqa: E402


def matches(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text.lower()))


def test_назначение_отдельно_от_поиска():
    phrase = "назначь репетицию с Витей и Людой в четверг в 20:00"
    assert matches(ASSIGN_Q, phrase)
    assert not matches(SLOTS_Q, phrase)


def test_поиск_окон_не_считается_назначением():
    phrase = "найди окна для Вити и Люды в четверг"
    assert matches(SLOTS_Q, phrase)
    assert not matches(ASSIGN_Q, phrase)


def test_назначение_идёт_раньше_ввода_данных():
    """Ш-52: вопросы разбираются до ввода; назначение — до бюджета."""
    rules = [name for name, _ in RULES]
    assert rules.index("assign") < rules.index("budget")
    assert rules.index("slots") < rules.index("assign")
    assert rules.index("schedule") < rules.index("assign")


def test_короткая_форма():
    assert matches(ASSIGN_Q, "поставь показ в пятницу в 19:00")
    assert matches(ASSIGN_Q, "запланируй тренинг во вторник в 18:00")

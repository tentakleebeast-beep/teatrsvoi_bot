"""Инструмент слоя А дизайн-ревью Mini App (docs/development/141 §4.1, §6).

Проверяются свойства, на которых держится доказательность замера:

* список экранов берётся из кода и совпадает со стендом (`PAGE_ROUTES`), а не из
  ручного словаря — иначе «все экраны» тихо превращаются в 20 из 30;
* у каждого экрана есть номер чек-листа: без него снимок `NNN-*.png` не находит
  строку отчёта;
* ролевая матрица различает «нет права словами», «403» и «пусто» — разница между
  ними и есть предмет ревью (принцип Б3);
* без аргументов скрипт печатает справку и выходит с кодом 2, а не падает
  трассировкой.

Обоснование: docs/development/141 §2, §4.1, §7.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def review():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ui_review as mod

    yield mod
    sys.path.remove(str(ROOT / "scripts"))


def test_инвентарь_из_кода_совпадает_со_стендом(review):
    inventory = review.build_inventory()
    stand = set(review.stand_inventory())
    assert len(inventory) == 30, f"экранов {len(inventory)}, а в чек-листе 30"
    assert set(inventory) == stand, "код и стенд описывают разные экраны"
    assert review.verify_inventory() == 0


def test_у_каждого_экрана_есть_номер_чеклиста(review):
    inventory = set(review.build_inventory())
    assert inventory == set(review.SCREEN_LABELS), (
        "нет номера: " + ", ".join(sorted(inventory - set(review.SCREEN_LABELS))))
    numbers = [value[0] for value in review.SCREEN_LABELS.values()]
    assert sorted(numbers) == list(range(1, 31)), "номера идут не подряд 1…30"
    slugs = [value[1] for value in review.SCREEN_LABELS.values()]
    assert len(set(slugs)) == 30, "имена снимков повторяются"


def test_десять_экранов_которых_нет_в_ux_audit(review):
    """Список из 141 §8 п. 1: эти экраны прежний замер не видел."""
    missing = {
        "/webapp/files", "/webapp/folder", "/webapp/notes", "/webapp/person",
        "/webapp/week/month", "/webapp/admin/t/archive", "/webapp/admin/t/dues",
        "/webapp/admin/t/invites", "/webapp/admin/t/notifications",
        "/webapp/admin/t/rights",
    }
    assert missing <= set(review.build_inventory())


def test_семь_редиректов_проверяются(review):
    assert len(review.REDIRECTS) == 7
    assert review.REDIRECTS["/webapp/schedule"] == "/webapp/week/summary"


@pytest.mark.parametrize("text,rows,expected", [
    ("Доступ запрещён. Код 403", 0, "403"),
    ("Нет права на этот раздел. Права выдаёт администратор.", 2, "нет права словами"),
    ("Заявки ждут решения", 4, "есть"),
    ("Пока пусто. Как заполнить — напишите организатору.", 0, "пусто"),
    ("", 0, "пусто"),
])
def test_ролевая_матрица_различает_состояния(review, text, rows, expected):
    assert review.classify_role_state(text, rows) == expected


def test_без_аргументов_справка_и_код_2(review):
    with pytest.raises(SystemExit) as exc:
        review.main([])
    assert exc.value.code == 2

"""Маска названий и палитра календаря: docs/development/16 §4.8, config/calendar-*.yaml."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fill():
    sys.path.insert(0, str(ROOT / "scripts"))
    import os

    cwd = os.getcwd()
    os.chdir(ROOT)                      # скрипт читает config/calendar-palette.yaml
    try:
        import calendar_fill_draft as mod
        yield mod
    finally:
        os.chdir(cwd)


@pytest.mark.parametrize("title,kind,name", [
    ("Валерий Аренда", "Аренда", "Валерий"),
    ("Аренда Валерий", "Аренда", "Валерий"),
    ("Показ аренда Добряки", "Показ", "Добряки"),
    ("Репа ЖН Наташа", "Репетиция", "Жизнь насекомых"),
    ("Репа Бунин", "Репетиция", "И всё же..."),
    ("Подготовка к ЛитРейву", "Репетиция", "ЛитРейв"),
    ("КУРС", "Тренинг", "Курс"),
    ("Музыка", "Тренинг", "Музыка"),
    ("Литературный вечер", "Показ", "Литературный вечер"),
])
def test_маска_названий(fill, title, kind, name):
    assert fill.classify(title) == (kind, name)


def test_аренда_и_показ_арендатора_дают_доход(fill):
    assert fill.TYPES["Аренда"]["revenue"] is True
    assert fill.TYPES["Показ"]["revenue"] is True
    assert fill.TYPES["Репетиция"]["revenue"] is False


def test_цвета_заданы_для_всех_типов(fill):
    for kind, meta in fill.TYPES.items():
        assert meta.get("color_id"), f"у типа {kind} нет цвета"
        assert 1 <= int(meta["color_id"]) <= 11

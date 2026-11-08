"""
Тексты интерфейса из файла: правка без релиза, ошибка файла — не падение.

Правила и требования раздела: docs/development/12-bot-behavior.md.
"""

from __future__ import annotations

from app import ui_texts


def test_тексты_читаются_из_репозитория() -> None:
    """config/ui-texts.yaml лежит в репозитории и заполнен осмысленно."""
    assert ui_texts.get("week", "save", "нет") != "нет"
    assert ui_texts.get("help", "free_text", "нет") != "нет"
    assert ui_texts.section("menu"), "меню без подписей"


def test_пропавшая_строка_даёт_умолчание() -> None:
    assert ui_texts.get("week", "нет-такой-строки", "умолчание") == "умолчание"
    assert ui_texts.get("нет-такой-секции", "save", "умолчание") == "умолчание"


def test_пустое_значение_тоже_умолчание() -> None:
    """Пустая строка в конфиге — это «не задано», а не пустая кнопка."""
    import pathlib
    import tempfile

    file = pathlib.Path(tempfile.mkdtemp()) / "ui-texts.yaml"
    file.write_text("week:\n  save: \"  \"\n", encoding="utf-8")
    ui_texts._CACHE["stamp"] = None
    old = ui_texts.path
    ui_texts.path = lambda: file
    try:
        assert ui_texts.get("week", "save", "умолчание") == "умолчание"
    finally:
        ui_texts.path = old
        ui_texts._CACHE["stamp"] = None


def test_битый_yaml_не_ломает_страницу() -> None:
    import pathlib
    import tempfile

    file = pathlib.Path(tempfile.mkdtemp()) / "ui-texts.yaml"
    file.write_text("week: [сломано\n", encoding="utf-8")
    old = ui_texts.path
    ui_texts.path = lambda: file
    try:
        assert ui_texts.get("week", "save", "умолчание") == "умолчание"
    finally:
        ui_texts.path = old
        ui_texts._CACHE["stamp"] = None

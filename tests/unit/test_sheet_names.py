"""
Поиск колонки человека в общем листе «Расписание студийцы» (Э5, FR-C9).

Одна функция имён на чтение и запись (docs/development/149 D9): если колонку находят по
фамилии с `ё`, по переставленным словам и по короткому псевдониму, то же правило работает
и при записи недели в лист. Проверяем на настоящих значениях замера (149 §2.3) — там
«Кондракова Алёна» из базы и «Алена Кондракова» в листе не находились, потому что
`ё` и `е` считались разными буквами.

Обоснование: FR-C9 (занятость дня в листе и базе), docs/development/149 §2.3, D9.
"""

from __future__ import annotations

from app.schedule_sheet import find_names_row, find_person_columns, resolve_column
from app.sheet_load import build_preview


def header(*names: str) -> list[list]:
    """Строка шапки как в живом листе: A — день недели, B — дата, C — «Решение», дальше люди."""
    return [["", "", "Решение", *names]]


def test_fr_c9_ё_и_е_это_одна_буква() -> None:
    """FR-C9: «Алёна» в базе и «Алена» в листе — один человек, а не «нет колонки»."""
    values = header("Алена Кондракова", "Даина Тишина")
    assert find_person_columns(values, 1, "Кондракова Алёна") == [4]
    assert find_person_columns(values, 1, "Тишина Даина") == [5]


def test_fr_c9_порядок_имени_не_важен() -> None:
    """FR-C9: люди подписаны то именем, то фамилией вперёд — колонка одна и та же."""
    values = header("Даина Тишина", "Мамлютов Виктор")
    assert find_person_columns(values, 1, "Тишина Даина") == [4]
    assert find_person_columns(values, 1, "Виктор Мамлютов") == [5]


def test_fr_c9_одно_общее_слово_из_двух_это_не_человек() -> None:
    """FR-C9: «Виктор Иванов» против «Виктор Мамлютов» — совпало только имя, колонки нет."""
    values = header("Виктор Мамлютов")
    assert find_person_columns(values, 1, "Виктор Иванов") == []


def test_fr_c9_однословное_имя_и_псевдоним_находят_колонку() -> None:
    """FR-C9: «Витя» в шапке — это псевдоним из `person_alias`, а не другое имя."""
    values = header("Витя", "Дарья Смирнова")
    assert find_person_columns(values, 1, "Виктор Мамлютов") == []
    assert find_person_columns(values, 1, "Виктор Мамлютов", aliases=["Витя"]) == [4]
    assert find_person_columns(values, 1, "Витя") == [4]


def test_fr_c9_две_подходящие_колонки_это_отказ_с_буквами() -> None:
    """FR-C9: два «Виктора Мамлютова» в шапке — не угадываем, а показываем буквы колонок."""
    values = header("Виктор Мамлютов", "Мамлютов Виктор")
    columns, names_row = resolve_column(values, "Виктор Мамлютов")
    assert names_row == 1
    assert columns == [4, 5]

    preview = build_preview(values, "Виктор Мамлютов", [], [], {})
    assert preview["ok"] is False
    assert preview["error"] == "person_ambiguous"
    assert preview["columns"] == [4, 5]


def test_fr_c9_человека_нет_в_шапке() -> None:
    """FR-C9: нет колонки — это `person_not_found`, а не «первая попавшаяся»."""
    values = header("Дарья Смирнова")
    columns, names_row = resolve_column(values, "Сторожук Юлия")
    assert (columns, names_row) == ([], 1)
    assert build_preview(values, "Сторожук Юлия", [], [], {}) == {
        "ok": False, "error": "person_not_found", "columns": []}


def test_fr_c9_шапка_находится_по_людям_а_не_по_решению() -> None:
    """FR-C9: шапка — это строка с людьми; «Решение» подписью человека себя не делает."""
    values = [["", "", "Решение"], ["", "09.11.2026", ""]]
    values[0] += ["Кондракова Алена"]
    assert find_names_row(values) == 1
    assert find_person_columns(values, 1, "Кондракова Алёна") == [4]

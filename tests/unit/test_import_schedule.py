"""Ш-03a плана 129: импорт расписания читает личные листы по данным БД, а не по словарю в коде.

`read_matrix` получает готовый поиск людей и обязан: взять лист, имя которого есть в данных
(`person_alias`/`person_key`), назвать лист, похожий на личную матрицу, но без человека,
и не трогать служебные листы. Файл — временный: боевой `Расписание СВОИ.xlsx` в тестах не нужен.
"""

from __future__ import annotations

from datetime import datetime

import import_schedule
import openpyxl
import pytest
from app.participant_names import Names, build_index


def _sheet_book(path, sheets, hours=()):
    """Книга-макет: у листов из `hours` — шапка с часами (как у личной матрицы)."""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, days in sheets.items():
        sheet = book.create_sheet(title)
        sheet.cell(row=1, column=2, value="Дата")
        if title in hours:
            for column, hour in enumerate(range(8, 16), start=3):
                sheet.cell(row=1, column=column, value=datetime(2026, 7, 1, hour))
        for row, day in enumerate(days, start=2):
            sheet.cell(row=row, column=2, value=datetime(2026, 7, day))
            for column in range(3, 11):
                sheet.cell(row=row, column=column, value=1)   # 1 = свободен
    book.save(path)
    return path


@pytest.fixture()
def index():
    participants = [{"id": 7, "display_name": "Соколов Марк", "person_key": "соколов-марк"}]
    aliases = [{"participant_id": 7, "alias": "Марк"}]
    return build_index(participants, aliases, Names())


def test_личный_лист_находится_по_данным_из_бд(tmp_path, monkeypatch, index):
    source = _sheet_book(tmp_path / "расписание.xlsx",
                         {"марк": [1], "итоги": [], "маша": [1], "Свод Пурга": [1]},
                         hours=("марк", "маша", "Свод Пурга"))
    monkeypatch.setattr(import_schedule, "SOURCE", str(source))
    monkeypatch.setattr(import_schedule, "DATE_FROM", import_schedule.date(2026, 7, 1))
    monkeypatch.setattr(import_schedule, "DATE_TO", import_schedule.date(2026, 7, 31))

    cells, sheets, unclaimed = import_schedule.read_matrix(index)

    assert sheets == ["марк"]
    assert {cell[0] for cell in cells} == {7}          # participant.id, не строка имени
    assert cells and all(cell[1].day == 1 for cell in cells)
    # всё, что не нашло человека, названо списком: сравнить с файлом и дописать в конфиг
    assert set(unclaimed) == {"итоги", "маша", "Свод Пурга"}

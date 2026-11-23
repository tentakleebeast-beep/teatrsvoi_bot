"""Имя листа занятости берётся по имени, «первый лист файла» запрещён (186 §4.3 шаг 4).

Обоснование: docs/development/186 §4.3, дефект R-3 (пустой `SCHEDULE_SHEET_TITLE`).
До карты пустая настройка означала «пиши в первый лист» — и бот писал в чужой лист.
Теперь имя приходит из привязки (или из настройки), а пустота — отказ.
"""

from __future__ import annotations

from app import sheet_binding


def test_имя_из_привязки_сильнее_настройки():
    binding = sheet_binding.Binding(key="schedule.workload", spreadsheet_id="s",
                                    sheet_title="Занятость", layout="monthly_blocks")
    assert binding.title == "Занятость"


def test_пустое_имя_в_привязке_не_превращается_в_первый_лист():
    props = [{"properties": {"sheetId": 1, "title": "Лист1"}},
             {"properties": {"sheetId": 2, "title": "Занятость"}}]
    try:
        sheet_binding.pick_sheet(props, "")
    except Exception as caught:  # noqa: BLE001 — важен код отказа
        assert getattr(caught, "code", "") == "sheet_ambiguous"
    else:
        raise AssertionError("пустое имя подставило первый лист файла")


def test_имя_сравнивается_без_хвостовых_пробелов():
    props = [{"properties": {"sheetId": 7, "title": "  Расписание "}}]
    assert sheet_binding.pick_sheet(props, "Расписание") == (7, "  Расписание ")


def test_два_листа_с_именем_настройки_это_отказ():
    props = [{"properties": {"sheetId": 1, "title": "Занятость"}},
             {"properties": {"sheetId": 2, "title": "Занятость"}}]
    try:
        sheet_binding.pick_sheet(props, "Занятость")
    except Exception as caught:  # noqa: BLE001
        assert getattr(caught, "code", "") == "sheet_ambiguous"
    else:
        raise AssertionError("двойник имени прошёл молча")

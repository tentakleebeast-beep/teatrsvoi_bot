"""Страница месяца листа занятости: шаблон `Зан. {month}` и отказ на пустой месяц.

Обоснование: FR-C9, FR-H9, docs/development/186 §4.3 (шаг 4) и §9 (`sheet_month_missing`);
карта Д-4 (доклад владельца 23.11.2026, docs/development/186 §4.3).

Правила, которые держит этот файл:

* в файле «Занятость» страница на каждый месяц своя (`Зан. 07.2026` … `Зан. 11.2026`),
  а строка `sheet_binding` одна — значит в привязке хранится шаблон, а месяц подставляет
  вызывающий;
* явное имя (`SCHEDULE_SHEET_TITLE` или имя без `{month}`) сильнее шаблона — обратная
  совместимость с уже засеянными привязками;
* нет страницы месяца — `sheet_month_missing` с именем страницы, а не `sheet_ambiguous`
  и не `binding_missing`: «лист не привязан» здесь было бы неправдой.
"""

from __future__ import annotations

import datetime as dt

import pytest
from app import core, sheet_binding

WEEK_MONTH = "Зан. 11.2026"


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeConn:
    """Соединение без БД: одна строка привязки по ключу."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self._params: tuple = ()

    def execute(self, sql, params=None):
        self._params = params or ()
        return self

    def fetchone(self):
        key = self._params[0] if self._params else ""
        return self.rows.get(key) if isinstance(key, str) else None


class FakeSheets:
    """Подделка Sheets API: список страниц и одна шапка на все страницы."""

    def __init__(self, titles: list[str], header: list[str] | None = None) -> None:
        self.titles = list(titles)
        self.header = list(header if header is not None else ["Дата", "День", "08", "09"])
        self.ranges: list[str] = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, fields=None, majorDimension=None):
        if fields:
            return _Req(lambda: {
                "properties": {"modifiedTime": "2026-11-01T00:00:00Z"},
                "sheets": [{"properties": {"sheetId": index + 1, "title": title}}
                           for index, title in enumerate(self.titles)]})
        self.ranges.append(str(range))
        return _Req(lambda: {"values": [self.header]})


def _binding(title: str = "", key: str = "schedule.workload") -> dict:
    return {"key": key, "spreadsheet_id": "s", "sheet_title": title,
            "layout": "monthly_blocks", "header_row": 1, "first_col": 1,
            "last_col": None, "header_checksum": None, "meta": {},
            "last_read_at": None, "last_edit_time": None}


def _fake_sheets(monkeypatch, titles: list[str]) -> FakeSheets:
    fake = FakeSheets(titles)
    monkeypatch.setattr(core, "_sheets", lambda: fake)
    monkeypatch.setattr(core, "_sheets_read", lambda: fake)
    monkeypatch.setattr(core, "SCHEDULE_SPREADSHEET_ID", "s")
    return fake


def test_шаблон_страницы_раскрывается_месяцем():
    """`Зан. {month}` и ноябрь 2026 — это страница `Зан. 11.2026`, а не шаблон."""
    assert sheet_binding.month_page_title("Зан. {month}", "2026-11") == WEEK_MONTH
    assert sheet_binding.month_page_title("Зан. {month}", dt.date(2026, 11, 3)) == WEEK_MONTH
    assert sheet_binding.month_page_title("Зан. {month}", "11.2026") == WEEK_MONTH


def test_имя_без_шаблона_месяц_не_меняет():
    """Явное имя сильнее шаблона: месяц его не трогает (обратная совместимость)."""
    assert sheet_binding.month_page_title("Зан. 10.2026", "2026-11") == "Зан. 10.2026"
    assert not sheet_binding.is_month_template("Зан. 10.2026")


def test_неделя_на_границе_месяцев_даёт_две_страницы():
    """26.10–01.11 живёт в двух страницах: молча читать одну — соврать про половину недели."""
    assert sheet_binding.month_pages(dt.date(2026, 10, 26)) == ["10.2026", "11.2026"]
    assert sheet_binding.month_pages(dt.date(2026, 11, 2)) == ["11.2026"]


def test_страница_месяца_находится_по_имени():
    props = [{"properties": {"sheetId": 1, "title": "Занятость"}},
             {"properties": {"sheetId": 2, "title": "Зан. 10.2026"}},
             {"properties": {"sheetId": 3, "title": WEEK_MONTH}}]
    assert sheet_binding.pick_month_sheet(props, "Зан. {month}", "2026-11") == (3, WEEK_MONTH)


def test_нет_страницы_месяца_это_sheet_month_missing():
    """Нет страницы месяца — свой код и имя страницы, а не «лист не определён»."""
    props = [{"properties": {"sheetId": 1, "title": "Зан. 10.2026"}}]
    with pytest.raises(Exception) as caught:  # noqa: BLE001 — важен код отказа
        sheet_binding.pick_month_sheet(props, "Зан. {month}", "2026-11")
    assert getattr(caught.value, "code", "") == "sheet_month_missing"
    assert "страницы месяца нет в листе" in caught.value.detail
    assert WEEK_MONTH in caught.value.detail


def test_две_страницы_одного_месяца_это_sheet_ambiguous():
    props = [{"properties": {"sheetId": 1, "title": WEEK_MONTH}},
             {"properties": {"sheetId": 2, "title": WEEK_MONTH}}]
    with pytest.raises(Exception) as caught:  # noqa: BLE001
        sheet_binding.pick_month_sheet(props, "Зан. {month}", "2026-11")
    assert getattr(caught.value, "code", "") == "sheet_ambiguous"


def test_явная_настройка_сильнее_шаблона(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "Зан. 10.2026")
    binding = sheet_binding.Binding(key="schedule.workload", spreadsheet_id="s",
                                    sheet_title="Зан. {month}", layout="monthly_blocks")
    assert sheet_binding.resolve_title("schedule.workload", binding, "2026-11") == "Зан. 10.2026"


def test_без_настройки_и_привязки_остаётся_шаблон_месяца(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    assert (sheet_binding.resolve_title("schedule.workload", None, "2026-11")
            == sheet_binding.DEFAULT_WORKLOAD_PAGE_PATTERN)


def test_open_sheet_берёт_страницу_месяца(monkeypatch):
    """Протокол открытия листа: имя из привязки-шаблона, страница — по месяцу недели."""
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    fake = _fake_sheets(monkeypatch, ["Занятость", "Зан. 10.2026", WEEK_MONTH])
    conn = FakeConn({"schedule.workload": _binding("Зан. {month}")})

    session = sheet_binding.open_sheet("schedule.workload", conn=conn, write=False,
                                       month="2026-11")

    assert session.sheet_title == WEEK_MONTH
    assert fake.ranges == [f"'{WEEK_MONTH}'!A1:AZ1"]


def test_open_sheet_без_страницы_месяца_отказывает(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    _fake_sheets(monkeypatch, ["Занятость", "Зан. 10.2026"])
    conn = FakeConn({"schedule.workload": _binding("Зан. {month}")})

    with pytest.raises(Exception) as caught:  # noqa: BLE001
        sheet_binding.open_sheet("schedule.workload", conn=conn, write=False, month="2026-11")

    assert getattr(caught.value, "code", "") == "sheet_month_missing"


def test_привязка_без_имени_и_без_настройки_раскрывается_месяцем(monkeypatch):
    """Пустая строка привязки — не «пиши в первый лист»: имя даёт шаблон месяца."""
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    _fake_sheets(monkeypatch, ["Лист1", WEEK_MONTH])
    conn = FakeConn({"schedule.workload": _binding("")})

    session = sheet_binding.open_sheet("schedule.workload", conn=conn, write=False,
                                       month="11.2026")

    assert session.sheet_title == WEEK_MONTH

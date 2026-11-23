"""Репетиция цветов: страницы тестового файла и люди из шапки живого листа.

Обоснование: docs/development/186 §13 п.3 (команда просмотра `--list`), §4.3 (страницы
не создаются, адрес пробы считается по раскладке), AC-10 (запись только ключом реестра
`schedule.test`). Карта Д-1: живой прогон показал, что флага `--list` не было, а люди
(`PEOPLE`/`NICKS`) были литералами и не совпадали ни с одной из пяти живых страниц
(`11.2026 · Даина`, `… · Гагарина`, `… · Кондракова`, `… · Таюкин`, `… · Загаринская`).

Что держит файл: страница месяца разбирается из имени листа, человек ищется по нику в
шапке живого листа, клетки `1`/`0` дают «весь день», текст дня разбирается как в сервисе,
отказы названы кодами, а `--list` существует и не пишет. Обе ветки (страницы есть/нет,
человек найден/нет) исполняются на подменённом клиенте: живой лист не читается.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app import core, schedule_rehearsal

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import fill_test_schedule as rehearsal  # noqa: E402

#: Живой лист так, как он устроен: B — дата, с колонки D — люди (149 §2.1, 186 §3).
LIVE = [
    ["Расписание студийцы"],
    ["№", "Дата", "День недели", "Даина Анна", "Гагарина Ольга", "Кондракова Мария"],
    ["1", "01.11.2026", "сб", "1", "0", "занят до 19:00"],
    ["2", "02.11.2026", "вс", "занят до 19:00", "", "не понятно"],
]


class Call:
    def __init__(self, value) -> None:
        self.value = value

    def execute(self):
        return self.value


class FakeValues:
    def __init__(self, owner: FakeService) -> None:
        self.owner = owner

    def get(self, spreadsheetId, range, majorDimension=None):  # noqa: A002 — имя поля API
        self.owner.ranges.append(range)
        return Call({"values": [list(row) for row in self.owner.live]})


class FakeSpreadsheets:
    def __init__(self, owner: FakeService) -> None:
        self.owner = owner

    def values(self):
        return FakeValues(self.owner)

    def get(self, spreadsheetId, fields=None, **kwargs):  # noqa: A002
        return Call({"sheets": [{"properties": {"sheetId": index, "title": title}}
                                for index, title in enumerate(self.owner.titles)]})


class FakeService:
    """Подменённый Google: имена листов тестового файла и значения живого листа."""

    def __init__(self, titles=(), live=None) -> None:
        self.titles = list(titles)
        self.live = [list(row) for row in (live if live is not None else LIVE)]
        self.ranges: list[str] = []

    def spreadsheets(self):
        return FakeSpreadsheets(self)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "human-sheet")
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SHEET_TITLE", "студийцы")
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "test-sheet")


def _page(title: str = "11.2026 · Даина") -> schedule_rehearsal.Page:
    page = schedule_rehearsal.parse_page(title)
    assert page is not None
    return page


def test_страница_месяца_разбирается_из_имени_листа():
    """`11.2026 · Даина` — страница; `студийцы (копия)` и голый месяц — нет."""
    page = _page()
    assert (page.month, page.nick) == ("11.2026", "Даина")
    assert schedule_rehearsal.parse_page("студийцы (копия)") is None
    assert schedule_rehearsal.parse_page("11.2026") is None
    assert schedule_rehearsal.parse_page("13.2026 · Никто") is None


def test_фильтр_по_месяцу_отбирает_только_свои_страницы():
    """`--month 2026-11` не трогает декабрь, живой лист этим не читается."""
    titles = ["11.2026 · Даина", "12.2026 · Гагарина", "студийцы (копия)"]
    assert [page.title for page in schedule_rehearsal.pages(titles, "2026-11")] == \
        ["11.2026 · Даина"]
    assert [page.title for page in schedule_rehearsal.pages(titles, "11.2026")] == \
        ["11.2026 · Даина"]
    assert [page.title for page in schedule_rehearsal.pages(titles)] == \
        ["11.2026 · Даина", "12.2026 · Гагарина"]


def test_list_показывает_страницы_тестового_файла(configured):
    """Ветка «страницы есть»: имена листов берутся из файла, человек — из шапки живого."""
    service = FakeService(titles=("11.2026 · Даина", "студийцы (копия)"))
    titles = rehearsal.test_titles(service)
    assert titles == ["11.2026 · Даина", "студийцы (копия)"]

    pages = schedule_rehearsal.pages(titles, "2026-11")
    assert len(pages) == 1
    values, reason = schedule_rehearsal.live_sheet_values(service)
    assert reason == "" and values == LIVE
    column, problem = schedule_rehearsal.person_column(values, pages[0].nick)
    assert problem == ""
    assert column == 4
    assert schedule_rehearsal.person_name(values, column) == "Даина Анна"


def test_list_без_страниц_месяца_даёт_код_1(configured):
    """Ветка «страниц нет»: в файле только служебный лист — список пуст, код выхода 1."""
    service = FakeService(titles=("студийцы (копия)",))
    titles = rehearsal.test_titles(service)
    assert schedule_rehearsal.pages(titles, "2026-11") == []


def test_флаг_list_существует_и_не_пишет():
    """Регресс Д-1: `--list` разбирается argparse, а с `--apply` отказывает кодом 2."""
    ap = rehearsal.build_parser()
    args = ap.parse_args(["--list", "--month", "2026-11"])
    assert args.list_only is True
    assert args.apply is False
    rehearsal.validate_args(ap, args)

    bad = ap.parse_args(["--list", "--apply", "--month", "2026-11"])
    with pytest.raises(SystemExit) as caught:
        rehearsal.validate_args(ap, bad)
    assert caught.value.code == 2


def test_пробы_берут_человека_из_шапки_и_разбирают_клетки(configured):
    """`1` — весь день занят, «занят до 19:00» — занят до 19 и свободен после."""
    values, reason = schedule_rehearsal.live_sheet_values(FakeService())
    assert reason == ""
    report = schedule_rehearsal.page_report(values, _page())

    assert report.ok, report.problems
    assert report.person == "Даина Анна"
    assert report.counts["busy"] == 16 + 11
    assert report.counts["free"] == 5
    by_cell = {(probe.day.day, probe.hour): probe.status for probe in report.probes}
    assert by_cell[(1, 8)] == "busy" and by_cell[(1, 23)] == "busy"
    assert by_cell[(2, 18)] == "busy" and by_cell[(2, 19)] == "free"
    assert schedule_rehearsal.month_days(values, "11.2026").keys() == \
        {probe.day for probe in report.probes}


def test_человека_нет_в_шапке_это_отказ(configured):
    """Ветка «человек не найден»: страница есть, колонки в живом листе нет."""
    values, _reason = schedule_rehearsal.live_sheet_values(FakeService())
    report = schedule_rehearsal.page_report(values, _page("11.2026 · Таюкин"))
    assert report.problems == ["person_not_found"]
    assert report.probes == []


def test_без_шапки_репетиция_не_угадывает_колонку(configured):
    """Нет строки имён — `header_not_found`, а не «первая колонка»."""
    report = schedule_rehearsal.page_report([["Расписание"]], _page())
    assert report.problems == ["header_not_found"]


def test_месяц_без_дней_в_листе_это_отказ(configured):
    """Страница декабря при ноябрьском листе: писать нечего, а не «ноль проб — успех»."""
    values, _reason = schedule_rehearsal.live_sheet_values(FakeService())
    report = schedule_rehearsal.page_report(values, _page("12.2026 · Даина"))
    assert report.problems == ["no_cells_in_month"]


def test_источник_живой_лист_а_не_файл(configured):
    """Чтение идёт по `SCHEDULE_HUMAN_*` тем же диапазоном, что у маршрута (149 §2.1)."""
    service = FakeService()
    values, reason = schedule_rehearsal.live_sheet_values(service)
    assert reason == ""
    assert service.ranges == [f"'{core.SCHEDULE_HUMAN_SHEET_TITLE}'!A1:AZ3000"]
    assert values[1][3] == "Даина Анна"


def test_без_настройки_живого_листа_источник_не_читается(monkeypatch):
    """Пустой `SCHEDULE_HUMAN_SPREADSHEET_ID` — `no_spreadsheet` без обращения к Google."""
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "")
    service = FakeService()
    assert schedule_rehearsal.live_sheet_values(service) == (None, "no_spreadsheet")
    assert service.ranges == []


def test_сбой_чтения_живого_листа_это_отказ_а_не_падение(configured, monkeypatch):
    """Клиент падает (нет ключа, нет прав) — `sheet_unavailable`, как у маршрута Э5."""
    def broken():
        raise OSError("нет ключа сервисного аккаунта")

    monkeypatch.setattr(core, "_sheets_read", broken)
    assert schedule_rehearsal.live_sheet_values() == (None, "sheet_unavailable")


def test_строка_отчёта_называет_человека_и_счёт(configured, capsys):
    """Печать плана: человек, колонка и разобранные клетки — без объектов `Probe`."""
    values, _reason = schedule_rehearsal.live_sheet_values(FakeService())
    rehearsal.print_page(schedule_rehearsal.page_report(values, _page()))
    out = capsys.readouterr().out
    assert "11.2026 · Даина" in out
    assert "человек «Даина Анна», колонка D" in out
    assert "проб 32" in out

    rehearsal.print_page(
        schedule_rehearsal.page_report(values, _page("11.2026 · Таюкин")))
    assert "нет колонки человека в листе" in capsys.readouterr().out

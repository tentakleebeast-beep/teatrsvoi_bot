"""Засев привязок листов: `--check`/`--apply` и «календарь — не таблица» (186 §4.3, AC-5).

Обоснование: docs/development/186 §4.3, AC-5; docs/development/190 §10.
`--check` обязан сверять строки `sheet_binding` с реестром (а не только читать Google):
именно этого не хватало — скрипт всегда возвращал код 1, потому что в список ключей
попадал `calendar.draft`, у которого нет таблицы. `--apply` обязан засеять и тестовый
лист цветов, у которого страниц много: страница берётся единственная под шаблоном или
названная `--page`, а при нескольких скрипт отказывает вместо «первой страницы».
"""

from __future__ import annotations

from contextlib import contextmanager

import google_seed_bindings as seed
from app import core


class FakeConn:
    """Соединение без БД: строки привязок по ключу."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self._params: tuple = ()
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or ()))
        self._params = params or ()
        return self

    def fetchone(self):
        key = self._params[0] if self._params else ""
        return self.rows.get(key) if isinstance(key, str) else None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


def _row(key: str, spreadsheet_id: str) -> dict:
    return {"key": key, "spreadsheet_id": spreadsheet_id,
            "sheet_title": "Транзакции", "layout": "flat_table", "header_row": 1,
            "first_col": 1, "last_col": None, "header_checksum": "abc123def456",
            "meta": {}, "last_read_at": None, "last_edit_time": None}


def _ids(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "bot-budget")
    monkeypatch.setattr(core, "SCHEDULE_SPREADSHEET_ID", "bot-schedule")
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "bot-test-sheet")
    return {"budget.transactions": "bot-budget", "schedule.workload": "bot-schedule",
            "schedule.test": "bot-test-sheet"}


def test_календарь_не_считается_листом():
    """`calendar.draft` — не таблица: в списке листов его быть не может."""
    assert "calendar.draft" not in seed.WRITE_SHEET_KEYS
    assert len(seed.WRITE_SHEET_KEYS) == 3


def test_проверка_на_пустой_базе_даёт_код_1(monkeypatch, capsys):
    """AC-5: нет строк — код 1 и список ключей, которые надо засеять."""
    _ids(monkeypatch)
    monkeypatch.setattr(core, "pool", FakePool(FakeConn({})))
    assert seed.main(["--check"]) == 1
    output = capsys.readouterr().out
    for key in seed.WRITE_SHEET_KEYS:
        assert key in output


def test_проверка_на_привязанной_базе_даёт_код_0(monkeypatch, capsys):
    """AC-5: три строки на месте — код 0."""
    ids = _ids(monkeypatch)
    rows = {key: _row(key, value) for key, value in ids.items()}
    monkeypatch.setattr(core, "pool", FakePool(FakeConn(rows)))
    assert seed.main(["--check"]) == 0
    assert "budget.transactions" in capsys.readouterr().out


def test_расхождение_файла_видно_проверкой(monkeypatch, capsys):
    """Привязка ведёт на другой файл — это расхождение, а не «на месте»."""
    ids = _ids(monkeypatch)
    rows = {key: _row(key, value) for key, value in ids.items()}
    rows["budget.transactions"]["spreadsheet_id"] = "старый-файл"
    monkeypatch.setattr(core, "pool", FakePool(FakeConn(rows)))
    assert seed.main(["--check"]) == 1
    assert "привязан другой файл" in capsys.readouterr().out


def test_пустая_сумма_шапки_это_расхождение(monkeypatch, capsys):
    """Без `header_checksum` запись в лист не начнётся — проверка это называет."""
    ids = _ids(monkeypatch)
    rows = {key: _row(key, value) for key, value in ids.items()}
    rows["schedule.workload"]["header_checksum"] = None
    monkeypatch.setattr(core, "pool", FakePool(FakeConn(rows)))
    assert seed.main(["--check"]) == 1
    assert "нет контрольной суммы шапки" in capsys.readouterr().out


def test_сверка_по_умолчанию_не_ходит_в_google(monkeypatch):
    """`--check` без ключа сервисного аккаунта работает: он читает только базу."""
    ids = _ids(monkeypatch)
    rows = {key: _row(key, value) for key, value in ids.items()}
    monkeypatch.setattr(core, "pool", FakePool(FakeConn(rows)))
    monkeypatch.setattr(core, "GOOGLE_SA_JSON", "")
    assert seed.main([]) == 0


def test_применение_без_ключа_отвечает_кодом_2(monkeypatch):
    """`--apply` без ключа Google — не молчаливый успех, а код 2."""
    _ids(monkeypatch)
    monkeypatch.setattr(core, "GOOGLE_SA_JSON", "")
    assert seed.main(["--apply"]) == 2


#: Страницы тестового листа цветов: у файла их столько, сколько людей в месяце.
PAGE = "11.2026 · Дарья"
OTHER_PAGE = "11.2026 · Орлова"


class _Call:
    """Ответ Google в форме `.execute()`."""

    def __init__(self, value: dict) -> None:
        self.value = value

    def execute(self) -> dict:
        return self.value


class _Values:
    def __init__(self, header: list) -> None:
        self.header = header
        self.ranges: list[str] = []

    def get(self, spreadsheetId, range, majorDimension=None):  # noqa: A002
        self.ranges.append(range)
        return _Call({"values": [self.header]})


class _Spreadsheets:
    def __init__(self, titles: tuple[str, ...], header: list) -> None:
        self.titles = titles
        self.values_api = _Values(header)

    def values(self) -> _Values:
        return self.values_api

    def get(self, spreadsheetId, fields=None):  # noqa: A002
        return _Call({"sheets": [{"properties": {"sheetId": index, "title": title}}
                                 for index, title in enumerate(self.titles)]})


class _Sheets:
    def __init__(self, titles: tuple[str, ...], header: list) -> None:
        self.spreadsheets_api = _Spreadsheets(titles, header)

    def spreadsheets(self) -> _Spreadsheets:
        return self.spreadsheets_api


def _google(monkeypatch, titles: tuple[str, ...], pattern: str = "{month} · {nick}") -> FakeConn:
    """Живой контур подменён: страницы тестового листа и шапка «Час»."""
    conn = FakeConn({})
    monkeypatch.setattr(core, "pool", FakePool(conn))
    monkeypatch.setattr(core, "GOOGLE_SA_JSON", "/tmp/sa.json")
    monkeypatch.setattr(core, "SCHEDULE_TEST_SHEET_PATTERN", pattern)
    monkeypatch.setattr(core, "_sheets_read", lambda: _Sheets(titles, ["Час"]))
    return conn


def _upserts(conn: FakeConn) -> list[tuple]:
    """Параметры записей в `sheet_binding` — их и сверяем на идемпотентность."""
    return [params for sql, params in conn.calls if sql.strip().startswith("insert into sheet_binding")]


def test_применение_засевает_единственную_страницу_по_шаблону(monkeypatch, capsys):
    """Шаблон `{month} · {nick}` и одна страница — засев идёт в неё, код 0 (AC-5)."""
    _ids(monkeypatch)
    conn = _google(monkeypatch, (PAGE,))

    assert seed.main(["--apply", "--only", "schedule.test"]) == 0
    assert f"ok: лист «{PAGE}»" in capsys.readouterr().out
    upserts = _upserts(conn)
    assert upserts and upserts[0][0] == "schedule.test" and upserts[0][2] == PAGE


def test_применение_при_двух_страницах_просит_page(monkeypatch, capsys):
    """Две страницы под шаблон — отказ и просьба назвать `--page`, а не «первая»."""
    _ids(monkeypatch)
    conn = _google(monkeypatch, (PAGE, OTHER_PAGE))

    assert seed.main(["--apply", "--only", "schedule.test"]) == 1
    output = capsys.readouterr().out
    assert "укажите --page" in output
    assert _upserts(conn) == []


def test_применение_с_page_засевает_названную_страницу(monkeypatch, capsys):
    """`--page` сильнее шаблона: привязка ведёт на названную страницу."""
    _ids(monkeypatch)
    conn = _google(monkeypatch, (PAGE, OTHER_PAGE))

    assert seed.main(["--apply", "--only", "schedule.test", "--page", OTHER_PAGE]) == 0
    assert f"лист «{OTHER_PAGE}»" in capsys.readouterr().out
    assert _upserts(conn)[0][2] == OTHER_PAGE


def test_применение_идемпотентно(monkeypatch):
    """Второй прогон не меняет ни `spreadsheet_id`, ни `header_checksum` (AC-5)."""
    _ids(monkeypatch)
    conn = _google(monkeypatch, (PAGE,))

    assert seed.main(["--apply", "--only", "schedule.test"]) == 0
    first = _upserts(conn)[-1]
    assert seed.main(["--apply", "--only", "schedule.test"]) == 0
    second = _upserts(conn)[-1]
    assert first == second


def test_засев_занятости_без_настройки_пишет_шаблон_месяца(monkeypatch):
    """Д-4: `--apply` без `SCHEDULE_SHEET_TITLE` засевает шаблон `Зан. {month}`.

    Одно статичное имя листа сломало бы сверку в следующем месяце (186 §4.3), поэтому
    в привязке живёт шаблон, а сумма шапки снимается с живой страницы месяца.
    """
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    props = [{"properties": {"title": "Занятость"}}, {"properties": {"title": "Зан. 10.2026"}}]

    title, problem = seed._sheet_title("schedule.workload", props, "", page="")
    assert (title, problem) == (seed.sheet_binding.DEFAULT_WORKLOAD_PAGE_PATTERN, "")

    page, problem = seed._workload_page(props, title)
    assert problem == "" and page == "Зан. 10.2026"

    _ids(monkeypatch)
    sheets = _Sheets(("Занятость", "Зан. 10.2026"), ["Дата", "День", "08", "09"])
    monkeypatch.setattr(core, "_sheets_read", lambda: sheets)
    conn = FakeConn({})

    outcome = seed._seed_one(conn, "schedule.workload", dry_run=False)

    assert outcome.startswith("ok"), outcome
    record = _upserts(conn)[-1]
    assert record[0] == "schedule.workload"
    assert record[2] == seed.sheet_binding.DEFAULT_WORKLOAD_PAGE_PATTERN
    assert record[7]                                   # сумма шапки не пустая
    assert sheets.spreadsheets_api.values_api.ranges == ["'Зан. 10.2026'!A1:AZ1"]


def test_засев_занятости_с_явным_именем_не_подменяется_шаблоном(monkeypatch):
    """Д-4: заданное `SCHEDULE_SHEET_TITLE` сильнее шаблона — обратная совместимость."""
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "Зан. 10.2026")
    assert seed._sheet_title("schedule.workload", [], "", page="") == ("Зан. 10.2026", "")


def test_засев_занятости_без_страниц_месяца_называет_причину(monkeypatch):
    """Нет ни одной страницы по шаблону — честная причина, а не «засеяно наугад»."""
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", "")
    page, problem = seed._workload_page([{"properties": {"title": "Занятость"}}],
                                        seed.sheet_binding.DEFAULT_WORKLOAD_PAGE_PATTERN)
    assert page == "" and "по шаблону" in problem

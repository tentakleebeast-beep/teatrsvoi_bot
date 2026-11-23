"""Запись тестового листа цветов ключом `schedule.test` (186 §4.3, AC-10).

Обоснование: docs/development/186 §3 (раскладка `hour_matrix`), §4.3 (шаги протокола),
AC-10 (страница по шаблону, отсутствующая — `sheet_ambiguous`, не создание),
§13.4 (запись в тестовый лист через рабочий путь).

Что держит файл: адрес клетки считается по строке дат и колонке часов, страницы не
создаются, запись идёт только с ключом реестра (`write_target_unset`/`sheet_ambiguous`),
повторная проба не пишет вторую клетку, а неподтверждённая запись не оставляет `row_map`.
"""

from __future__ import annotations

from datetime import date

import pytest
from app import core, schedule_test_sheet
from app.errors import ApiError

PAGE = "11.2026 · Дарья"
SPREADSHEET = "test-sheet"

BINDING = {"key": "schedule.test", "spreadsheet_id": SPREADSHEET, "sheet_title": "",
           "layout": "hour_matrix", "header_row": 1, "first_col": 1, "last_col": 1,
           "header_checksum": None, "meta": {}, "last_read_at": None,
           "last_edit_time": None}

#: Страница раскладки: строка 1 — даты, колонка A — часы.
GRID = [["Час", "01.11.2026", "02.11.2026"],
        ["08:00", "", ""],
        ["09:00", "", ""]]


class FakeConn:
    """Соединение без БД: отдаёт строку привязки, помнит запросы."""

    def __init__(self, binding: dict | None = BINDING, row_maps: dict | None = None) -> None:
        self.binding = binding
        self.row_maps = row_maps or {}
        self.calls: list[tuple[str, tuple]] = []
        self.sql = ""

    def execute(self, sql, params=None):
        self.calls.append((sql, params or ()))
        self.sql = " ".join(sql.split()).lower()
        return self

    def fetchone(self):
        if "from sheet_binding" in self.sql:
            return self.binding
        if "from row_map" in self.sql:
            return self.row_maps.get(self.calls[-1][1])
        return None

    def writes(self, table: str) -> list[str]:
        return [sql for sql, _ in self.calls if f"into {table}" in sql or f"update {table}" in sql]


class Call:
    def __init__(self, value=None) -> None:
        self.value = value if value is not None else {}

    def execute(self):
        return self.value


class FakeValues:
    def __init__(self, owner: FakeService) -> None:
        self.owner = owner

    def get(self, spreadsheetId, range, majorDimension=None):  # noqa: A002 — имя поля API
        self.owner.read_ranges.append(range)
        if range.endswith(":AZ1"):
            return Call({"values": [self.owner.grid[0]]})
        return Call({"values": self.owner.grid})

    def batchUpdate(self, spreadsheetId, body):  # noqa: A002
        for item in body.get("data") or []:
            a1 = str(item["range"]).split("!")[-1]
            self.owner.text[a1] = (item.get("values") or [[""]])[0][0]
        return Call()

    def batchGet(self, spreadsheetId, ranges):  # noqa: A002
        out = []
        for item in ranges:
            a1 = str(item).split("!")[-1]
            if a1 in self.owner.broken:
                out.append({"values": [[self.owner.broken[a1]]]})
            else:
                out.append({"values": [[self.owner.text.get(a1, "")]]})
        return Call({"valueRanges": out})


class FakeSpreadsheets:
    def __init__(self, owner: FakeService) -> None:
        self.owner = owner
        self.values_api = FakeValues(owner)

    def values(self):
        return self.values_api

    def get(self, spreadsheetId, fields=None, ranges=None, includeGridData=None):  # noqa: A002
        if includeGridData:
            return Call({"sheets": [{"data": [{"rowData": self.owner.row_data()}]}]})
        return Call({"sheets": [{"properties": {"sheetId": 7, "title": title}}
                                for title in self.owner.titles],
                     "properties": {}})

    def batchUpdate(self, spreadsheetId, body):  # noqa: A002
        for request in body.get("requests") or []:
            self.owner.requests.append(request)
            cell = request["repeatCell"]
            rng = cell["range"]
            key = (rng["startRowIndex"] + 1, rng["startColumnIndex"] + 1)
            self.owner.colors[key] = cell["cell"]["userEnteredFormat"]["backgroundColor"]
        return Call()


class FakeService:
    """Поддельный Google: метаданные, значения, текст, фон и сетка цветов."""

    def __init__(self, grid=None, titles=(PAGE,), broken=None) -> None:
        self.grid = [list(row) for row in (grid or GRID)]
        self.titles = list(titles)
        self.broken = broken or {}
        self.text: dict[str, str] = {}
        self.colors: dict[tuple[int, int], dict] = {}
        self.requests: list[dict] = []
        self.read_ranges: list[str] = []
        self.sheets_api = FakeSpreadsheets(self)

    def spreadsheets(self):
        return self.sheets_api

    def row_data(self) -> list[dict]:
        if not self.colors:
            return []
        max_row = max(row for row, _ in self.colors)
        max_col = max(col for _, col in self.colors)
        rows = []
        for row in range(1, max_row + 1):
            cells = []
            for col in range(1, max_col + 1):
                color = self.colors.get((row, col))
                cells.append({"userEnteredFormat": {"backgroundColor": color}} if color else {})
            rows.append({"values": cells})
        return rows


class FakeDrive:
    """Метаданные файла: `modifiedTime` отдаётся Drive, а не Sheets (L-052)."""

    def __init__(self, modified="2026-11-01T10:00:00Z") -> None:
        self.modified = modified

    def files(self):
        return self

    def get(self, fileId, fields=None):  # noqa: A002
        return Call({"modifiedTime": self.modified})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", SPREADSHEET)
    monkeypatch.setattr(core, "SCHEDULE_TEST_SHEET_PATTERN", "{month} · {nick}")


def _probe(hour: int = 9, day: date = date(2026, 11, 1), status: str = "busy"):
    return schedule_test_sheet.Probe(day=day, hour=hour, status=status)


def test_шаблон_страницы_разворачивает_месяц_и_ник():
    """Страница — «MM.YYYY · Ник», как подписал человек; готовое имя не трогается."""
    assert (schedule_test_sheet.page_title("{month} · {nick}", "11.2026", "Дарья")
            == PAGE)
    assert schedule_test_sheet.page_title("11.2026 · Ник", "11.2026", "Дарья") == "11.2026 · Ник"


def test_пустой_шаблон_даёт_штатную_страницу():
    """Пустая настройка (дефолт `.env`) — это «MM.YYYY · Ник», а не пустое имя (186 §2.2).

    Раньше пустое значение давало `sheet_ambiguous` на каждом вызове: владелец задал бы
    только id файла, а маршрут и `fill_test_schedule.py` отказывали бы всегда.
    """
    assert schedule_test_sheet.page_title("", "11.2026", "Дарья") == PAGE
    assert schedule_test_sheet.page_title("   ", "11.2026", "Дарья") == PAGE
    assert schedule_test_sheet.page_title(None, "11.2026", "Дарья") == PAGE


def test_пустая_настройка_пишет_страницу_по_умолчанию(monkeypatch):
    """Маршрут при пустом `SCHEDULE_TEST_SHEET_PATTERN` доходит до записи, а не отказывает."""
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", SPREADSHEET)
    monkeypatch.setattr(core, "SCHEDULE_TEST_SHEET_PATTERN", "")
    conn = FakeConn()
    service = FakeService()
    report = schedule_test_sheet.write_probes(conn, month="11.2026", nick="Дарья",
                                              probes=[_probe()], service=service,
                                              drive=FakeDrive())
    assert report["sheet"] == PAGE
    assert report["written"] == 1
    assert service.text == {"B3": "занят"}


def test_месяц_принимается_в_двух_видах():
    """Экран и скрипт зовут месяц по-разному — страница от этого не меняется."""
    assert schedule_test_sheet.month_label("2026-11") == "11.2026"
    assert schedule_test_sheet.month_label("11.2026") == "11.2026"


def test_адрес_пробы_считается_по_датам_и_часам():
    """Адрес: колонка из строки дат, строка из колонки часов (186 §3)."""
    plan = schedule_test_sheet.plan_probes(GRID, PAGE, [_probe(hour=9, day=date(2026, 11, 2))])
    assert plan.ok
    cell = plan.cells[0]
    assert cell.a1 == "C3"
    assert cell.entity_id == f"{PAGE}:2026-11-02:09"
    assert cell.cell_key == "2026-11-02T09"
    assert cell.text == "занят"
    assert cell.color == schedule_test_sheet.STATUSES["busy"]["color"]


def test_дата_вне_страницы_это_отказ_раскладки():
    """Дата, которой нет в шапке, не создаётся — `plan_problems`."""
    plan = schedule_test_sheet.plan_probes(GRID, PAGE, [_probe(day=date(2026, 12, 1))])
    assert plan.problems == ["day_not_in_page: 2026-12-01"]


def test_часа_вне_раскладки_нет():
    """Час вне 08:00…23:00 не додумывается до строки."""
    plan = schedule_test_sheet.plan_probes(GRID, PAGE, [_probe(hour=23)])
    assert plan.problems == ["hour_not_in_page: 23"]


def test_нет_страницы_отказ_и_страница_не_создаётся(configured):
    """Отсутствующая страница — `sheet_ambiguous`: `addSheet` не вызывается."""
    service = FakeService(titles=("11.2026 · Орлова",))
    with pytest.raises(ApiError) as caught:
        schedule_test_sheet.write_probes(FakeConn(), month="11.2026", nick="Дарья",
                                         probes=[_probe()], service=service,
                                         drive=FakeDrive(), apply=False)
    assert caught.value.code == "sheet_ambiguous"
    assert not [req for req in service.requests if "addSheet" in req]


def test_две_страницы_с_одним_именем_отказ(configured):
    """Два листа с одним именем — писать некуда: `sheet_ambiguous`."""
    service = FakeService(titles=(PAGE, PAGE))
    with pytest.raises(ApiError) as caught:
        schedule_test_sheet.write_probes(FakeConn(), month="11.2026", nick="Дарья",
                                         probes=[_probe()], service=service,
                                         drive=FakeDrive(), apply=False)
    assert caught.value.code == "sheet_ambiguous"


def test_без_идентификатора_лист_не_пишется(configured, monkeypatch):
    """`SCHEDULE_TEST_SPREADSHEET_ID` пуст — `write_target_unset` (503), Google не зовётся."""
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "")
    service = FakeService()
    with pytest.raises(ApiError) as caught:
        schedule_test_sheet.write_probes(FakeConn(), month="11.2026", nick="Дарья",
                                         probes=[_probe()], service=service,
                                         drive=FakeDrive(), apply=True)
    assert caught.value.status_code == 503
    assert caught.value.code == "write_target_unset"
    assert service.read_ranges == []


def test_запись_подтверждается_цветом_и_оставляет_row_map(configured):
    """Успешная проба: текст и фон записаны, перечитаны, `row_map` получил адрес и хеш."""
    conn = FakeConn()
    service = FakeService()
    report = schedule_test_sheet.write_probes(conn, month="11.2026", nick="Дарья",
                                              probes=[_probe()], actor_id=5,
                                              service=service, drive=FakeDrive())
    assert report["applied"] is True
    assert report["written"] == 1
    assert service.text == {"B3": "занят"}
    assert service.colors == {(3, 2): schedule_test_sheet.STATUSES["busy"]["color"]}
    assert conn.writes("row_map"), "адрес записи не сохранён"
    assert conn.writes("sheet_binding"), "отметка чтения не обновлена"
    assert conn.writes("audit_log") or any("audit" in sql for sql, _ in conn.calls)


def test_повторная_проба_не_пишет_второй_раз(configured):
    """Тот же хеш в `row_map` — пропуск: второй записи нет."""
    plan_cell = schedule_test_sheet.plan_probes(GRID, PAGE, [_probe()]).cells[0]
    conn = FakeConn(row_maps={("schedule.test", "color_probe", plan_cell.entity_id,
                               plan_cell.cell_key):
                              {"id": 1, "sheet_row": 3, "col_from": 2, "col_to": 2,
                               "cell_key": plan_cell.cell_key,
                               "content_hash": plan_cell.content_hash, "is_deleted": False}})
    service = FakeService()
    report = schedule_test_sheet.write_probes(conn, month="11.2026", nick="Дарья",
                                              probes=[_probe()], service=service,
                                              drive=FakeDrive())
    assert report["applied"] is False
    assert report["skipped"] == 1
    assert service.text == {}
    assert not conn.writes("row_map")


def test_другая_проба_правит_клетку_а_не_добавляет(configured):
    """Хеш другой — та же клетка перезаписывается (адрес из `row_map`, не вторая строка)."""
    plan_cell = schedule_test_sheet.plan_probes(GRID, PAGE, [_probe()]).cells[0]
    conn = FakeConn(row_maps={("schedule.test", "color_probe", plan_cell.entity_id,
                               plan_cell.cell_key):
                              {"id": 1, "sheet_row": 3, "col_from": 2, "col_to": 2,
                               "cell_key": plan_cell.cell_key,
                               "content_hash": "старый-хеш", "is_deleted": False}})
    service = FakeService()
    report = schedule_test_sheet.write_probes(
        conn, month="11.2026", nick="Дарья",
        probes=[_probe(status="free")], service=service, drive=FakeDrive())
    assert report["applied"] is True
    assert service.text == {"B3": "свободен"}
    assert conn.writes("row_map")


def test_неподтверждённая_запись_не_оставляет_row_map(configured):
    """Лист вернул другое значение — `sheet_verify_failed`, адрес не запоминается."""
    conn = FakeConn()
    service = FakeService(broken={"B3": "что-то другое"})
    with pytest.raises(ApiError) as caught:
        schedule_test_sheet.write_probes(conn, month="11.2026", nick="Дарья",
                                         probes=[_probe()], service=service,
                                         drive=FakeDrive())
    assert caught.value.code == "sheet_verify_failed"
    assert not conn.writes("row_map")


def test_план_без_apply_ничего_не_пишет(configured):
    """`apply=False` — только план: ни текста, ни цвета, ни `row_map`."""
    conn = FakeConn()
    service = FakeService()
    report = schedule_test_sheet.write_probes(conn, month="11.2026", nick="Дарья",
                                              probes=[_probe()], service=service,
                                              drive=FakeDrive(), apply=False)
    assert report["applied"] is False
    assert service.text == {} and service.requests == []
    assert not conn.writes("row_map")

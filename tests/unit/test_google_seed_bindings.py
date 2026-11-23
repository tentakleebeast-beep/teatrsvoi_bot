"""Засев привязок листов: `--check`/`--apply` и «календарь — не таблица» (186 §4.3, AC-5).

Обоснование: docs/development/186 §4.3, AC-5; docs/development/190 §10.
`--check` обязан сверять строки `sheet_binding` с реестром (а не только читать Google):
именно этого не хватало — скрипт всегда возвращал код 1, потому что в список ключей
попадал `calendar.draft`, у которого нет таблицы.
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

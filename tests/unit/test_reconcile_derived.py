"""Сверка «файл → БД» не правит производные от события строки (AC-25).

Спецификация `docs/development/196` §7.4 п.2: строка, у которой есть `event_id`,
принадлежит событию. Если лист её «поправит», `origin='import'` уничтожит связь
с событием и инварианты I11/I12 разойдутся молча. Поэтому решение живёт в трёх
местах, и каждое проверяется здесь исполнением, а не чтением SQL глазами:

* `triage_missing` — строка с `event_id` не считается удалённой человеком и не уходит
  в `rejected`;
* `reconcile_budget` — её расхождение попадает в «производные_от_события», а не
  в «изменено», и `--apply` не выпускает по ней update;
* `read_finance_sheet.apply_updates` — правка листа не двигает такую строку: условие
  `event_id is null` стоит в самом update.

Обоснование: AC-25, `docs/development/196-e6-projects-spec.md` §7.4, §12.
Живая БД не нужна: соединение и лист подменяются (образец — `test_reconcile_helpers.py`).
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rec():
    sys.path.insert(0, str(ROOT / "scripts"))
    import reconcile as mod

    yield mod


@pytest.fixture(scope="module")
def read():
    sys.path.insert(0, str(ROOT / "scripts"))
    import read_finance_sheet as mod

    yield mod


class _Result:
    """Курсор-двойник: только то, что читает `reconcile_budget`."""

    def __init__(self, rows: list[dict] | None = None, row: dict | None = None,
                 rowcount: int = 1) -> None:
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._row


class _Conn:
    """Соединение-двойник: отдаёт строки `budget_tx` и запоминает все update."""

    def __init__(self, db_rows: list[dict]) -> None:
        self.db_rows = db_rows
        self.updates: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple | None = None):
        if sql.lstrip().startswith("select t.id"):
            return _Result(rows=self.db_rows)
        if "select id from category" in sql:
            return _Result(row={"id": 7})
        if sql.lstrip().startswith("update"):
            self.updates.append((sql, params or ()))
        return _Result()

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _file_row(tx_id: int, amount: float) -> dict:
    return {"id": tx_id, "date": date(2026, 11, 6), "type": "расход", "block": "expense",
            "amount": amount, "category": "Аренда помещения", "project": "",
            "counterparty": "", "purpose": "Аренда Прачки"}


def _db_row(tx_id: int, amount: float, *, event_id: int | None = None,
            origin: str = "file") -> dict:
    return {"id": tx_id, "tx_date": date(2026, 11, 6), "amount": Decimal(str(amount)),
            "tx_type": "expense", "status": "confirmed", "origin": origin,
            "category_id": 7, "sheet_written_at": "2026-11-06T10:00:00",
            "sheet_error": None, "event_id": event_id, "category": "Аренда помещения"}


def _stand(rec, monkeypatch, file_rows: list[dict], db_rows: list[dict]) -> _Conn:
    conn = _Conn(db_rows)
    monkeypatch.setattr(rec, "read_tx_sheet", lambda sheets: file_rows)
    monkeypatch.setattr(rec.psycopg, "connect", lambda *a, **k: conn)
    return conn


def _updates_of_amount(conn: _Conn) -> list[tuple[str, tuple]]:
    return [(sql, params) for sql, params in conn.updates if "update budget_tx set amount" in sql]


def test_производная_строка_не_в_изменено_и_видна_в_отчёте(rec, monkeypatch):
    """AC-25: в листе 14 000, в базе 15 000 у строки с `event_id` — база не меняется.

    Ожидаемое из спецификации: `amount` остался 15000, `origin` остался `event`,
    расхождение попало в отчёт отдельной категорией «производные_от_события».
    """
    conn = _stand(rec, monkeypatch,
                  file_rows=[_file_row(12, 14000.0), _file_row(13, 50000.0)],
                  db_rows=[_db_row(12, 15000.0, event_id=99, origin="event"),
                           _db_row(13, 40000.0)])

    report = rec.reconcile_budget(sheets=None, apply=True)

    # Производная строка не в «изменено»: её расхождение только показано.
    assert [item["id"] for item in report["изменено"]] == [13]
    assert report["расхождения"] == 1

    derived = report["производные_от_события"]
    assert [item["id"] for item in derived] == [12], "расхождение не попало в отчёт"
    assert derived[0]["расхождения"]["amount"] == (15000.0, 14000.0)
    assert report["производных"] == 1

    # По производной строке update не выпускался вовсе — ни amount, ни origin.
    touched = [params[-1] for _sql, params in _updates_of_amount(conn)]
    assert touched == [13], f"update по производной строке: {touched}"


def test_производная_строка_не_отклоняется_как_удалённая(rec):
    """Лист её не «удалял»: статус строки держит событие, а не файл."""
    derived = {"id": 12, "origin": "event", "sheet_written_at": "2026-11-06T10:00:00",
               "sheet_error": None, "event_id": 99}
    hand = {"id": 13, "origin": "file", "sheet_written_at": "2026-11-06T10:00:00",
            "sheet_error": None, "event_id": None}

    rejected, unconfirmed = rec.triage_missing([derived, hand], set())

    assert [row["id"] for row in rejected] == [13], "производная ушла в rejected"
    assert unconfirmed == []


def test_строка_без_event_id_правится_как_раньше(rec, monkeypatch):
    """Обычная строка листа живёт по-прежнему: расхождение — «изменено» и update."""
    conn = _stand(rec, monkeypatch,
                  file_rows=[_file_row(13, 50000.0)],
                  db_rows=[_db_row(13, 40000.0)])

    report = rec.reconcile_budget(sheets=None, apply=True)

    assert report["изменено"] == [{"id": 13, "что": {"amount": (40000.0, 50000.0)}}]
    assert report["производные_от_события"] == []
    assert report["производных"] == 0

    amount_updates = _updates_of_amount(conn)
    assert len(amount_updates) == 1
    sql, params = amount_updates[0]
    assert params[0] == 50000.0
    assert params[-1] == 13
    assert "origin = 'import'" in sql, "правка листа не помечает строку импортом"


class _GuardedTable:
    """Двойник `budget_tx` для `apply_updates`: исполняет условие `event_id is null`.

    Возвращает `rowcount=0`, если строка производная, — так же, как Postgres не
    находит строку под это условие. Так проверка исполняет решение, а не сверяет текст.
    """

    def __init__(self, rows: dict[int, dict]) -> None:
        self.rows = rows

    def execute(self, sql: str, params: list):
        assert sql.startswith("update budget_tx set"), sql
        tx_id = params[-1]
        row = self.rows.get(tx_id)
        if row is None:
            return _Result(rowcount=0)
        if "event_id is null" in sql and row["event_id"] is not None:
            return _Result(rowcount=0)
        if "amount = %s" in sql:
            row["amount"] = params[0]
        return _Result(rowcount=1)


def test_правка_листа_не_двигает_производную_строку(read):
    """AC-25: `apply_updates` обновляет обычную строку и молчит про производную."""
    table = {12: {"amount": 15000.0, "event_id": 99},
             13: {"amount": 40000.0, "event_id": None}}
    updates = [
        {"id": 12, "row": 88, "change": {"amount": {"было": 15000.0, "стало": 14000.0}}},
        {"id": 13, "row": 89, "change": {"amount": {"было": 40000.0, "стало": 50000.0}}},
    ]

    changed = read.apply_updates(updates, _GuardedTable(table))

    assert changed == 1, "производная строка засчитана обновлённой"
    assert table[12]["amount"] == 15000.0, "правка листа переписала сумму события"
    assert table[13]["amount"] == 50000.0

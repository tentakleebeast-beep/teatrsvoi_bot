"""Дедуп по `row_map`: повтор операции не создаёт вторую строку (docs/development/186 §4.3).

Обоснование: docs/development/186 §4.3 шаги 10–11, `FR-C3` (идемпотентность записи).
Строка адресуется сущностью, а не номером: номер строки двигается, `entity_id` — нет.
Тест гоняет `row_map_save`/`row_map_find` на соединении-двойнике и проверяет, что
второй вызов обновляет адрес и хеш, а не добавляет новую запись.
"""

from __future__ import annotations

import re

from app import sheet_binding


class FakeConn:
    """Соединение без БД: помнит одну строку `row_map` и запросы к ней."""

    def __init__(self) -> None:
        self.row: dict | None = None
        self.calls: list[str] = []

    def execute(self, sql, params=None):
        self.calls.append(" ".join(sql.split()))
        self.params = params
        return self

    def fetchone(self):
        return self.row


def test_первая_запись_создаёт_адрес():
    conn = FakeConn()
    sheet_binding.row_map_save(conn, "budget.transactions", "budget_tx", "42",
                               sheet_row=7, col_from=1, col_to=13, content_hash="h1")
    assert any(call.lower().startswith("insert into row_map") for call in conn.calls)
    assert conn.params[0] == "budget.transactions"
    assert conn.params[3] == 7


def test_повтор_обновляет_адрес_а_не_добавляет_строку():
    conn = FakeConn()
    conn.row = {"id": 5, "sheet_row": 7, "col_from": 1, "col_to": 13,
                "cell_key": None, "content_hash": "h1", "is_deleted": False}
    sheet_binding.row_map_save(conn, "budget.transactions", "budget_tx", "42",
                               sheet_row=9, content_hash="h2")
    updates = [call for call in conn.calls if call.lower().startswith("update row_map")]
    assert updates, "повтор операции обязан править существующую строку"
    assert not [call for call in conn.calls if call.lower().startswith("insert into row_map")]
    assert conn.params[0] == 9


def test_адрес_ищется_с_учётом_клетки_матрицы():
    """Для матрицы занятости ключ — ещё и `cell_key`: день и час."""
    conn = FakeConn()
    sheet_binding.row_map_find(conn, "schedule.workload", "availability_cell", "17",
                               cell_key="2026-11-10T19")
    sql = conn.calls[0].lower()
    assert "cell_key" in sql and "coalesce" in sql


def test_хеш_содержимого_устойчив_к_порядку_ключей():
    """`content_hash` — про значения, а не про порядок полей в словаре."""
    left = sheet_binding.content_hash({"b": 2, "a": 1})
    right = sheet_binding.content_hash({"a": 1, "b": 2})
    assert left == right and re.fullmatch(r"[0-9a-f]{64}", left)

"""Свежесть источников: «данные от …» или честное «неизвестно» (docs/development/186 §7).

Обоснование: docs/development/186 §7; уточнение E5-3 (`docs/development/188` §10).
Прошлый снимок никогда не выдаётся за свежий: у каждого источника есть `as_of`,
`last_edit_time`, `stale` и `error`, и нет поля «значения» — в отчёт не утекают данные.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import calendar_sync


class FakeConn:
    """Соединение без БД: привязки листов и отметка чтения календаря."""

    def __init__(self, bindings: dict[str, dict] | None = None,
                 read_at: str | None = None) -> None:
        self.bindings = bindings or {}
        self.read_at = read_at
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or ()))
        self._params = params or ()
        return self

    def fetchone(self):
        sql = self.calls[-1][0].lower()
        if sql.startswith("select value from app_setting"):
            return {"value": self.read_at} if self.read_at else None
        key = self._params[0] if self._params else None
        return self.bindings.get(key)

    def fetchall(self):
        return []


def _row(read_at: datetime | None, edit_at: datetime | None, key: str = "budget.live") -> dict:
    return {"key": key, "spreadsheet_id": "s", "sheet_title": "Т", "layout": "flat_table",
            "header_row": 1, "first_col": 1, "last_col": None, "header_checksum": None,
            "meta": {}, "last_read_at": read_at, "last_edit_time": edit_at}


def _entry(items: list[dict], key: str) -> dict:
    return next(item for item in items if item["key"] == key)


def test_источник_без_привязки_честно_неизвестен():
    items = calendar_sync.freshness(FakeConn())
    entry = _entry(items, "budget.live")
    assert entry["error"] == "binding_missing" and entry["stale"] is True
    assert entry["as_of"] is None


def test_свежее_чтение_считается_свежим():
    now = datetime.now(UTC)
    conn = FakeConn({"budget.live": _row(now - timedelta(minutes=1), now - timedelta(days=1))})
    entry = _entry(calendar_sync.freshness(conn), "budget.live")
    assert entry["stale"] is False and entry["as_of"] and entry["last_edit_time"]


def test_старое_чтение_помечено_stale():
    old = datetime.now(UTC) - timedelta(days=3)
    conn = FakeConn({"budget.live": _row(old, old)})
    assert _entry(calendar_sync.freshness(conn), "budget.live")["stale"] is True


def test_календарь_без_отметки_чтения_несвежий():
    items = calendar_sync.freshness(FakeConn())
    entry = _entry(items, "calendar.read")
    assert entry["error"] is None and entry["stale"] is True and entry["as_of"] is None


def test_календарь_с_отметкой_чтения_свежий():
    now = datetime.now(UTC).isoformat()
    items = calendar_sync.freshness(FakeConn(read_at=now))
    entry = _entry(items, "calendar.read")
    assert entry["stale"] is False and entry["as_of"]


def test_в_отчёте_нет_поля_со_значениями():
    """Свежесть — это время, а не данные: клетки и суммы в отчёт не попадают."""
    items = calendar_sync.freshness(FakeConn())
    for entry in items:
        assert set(entry) <= {"key", "as_of", "last_edit_time", "stale", "error"}

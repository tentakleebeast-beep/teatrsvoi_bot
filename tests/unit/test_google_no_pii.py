"""Отчёты Google не несут персональных данных (docs/development/186 §3, §7).

Обоснование: docs/development/186 §3 (живые значения не логируются), §7, `S-…`
требование приватности `docs/architecture/11-business-rules.md`.
`/tools/google/freshness` и `/tools/google/refresh` отвечают временем и адресами,
а не содержимым клеток и не именами людей: отчёт должен оставаться отчётом.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import calendar_sync

FORBIDDEN = ("Дарья", "Смирнова", "+7", "@", "Иванов")


class FakeConn:
    def __init__(self, bindings: dict | None = None, read_at: str | None = None) -> None:
        self.bindings = bindings or {}
        self.read_at = read_at
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or ()))
        return self

    def fetchone(self):
        sql = self.calls[-1][0].lower()
        if sql.startswith("select value from app_setting"):
            return {"value": self.read_at} if self.read_at else None
        key = (self.calls[-1][1] or ("",))[0]
        return self.bindings.get(key)

    def fetchall(self):
        return []


def _row(key: str) -> dict:
    now = datetime.now(UTC) - timedelta(minutes=1)
    return {"key": key, "spreadsheet_id": "s", "sheet_title": "Т", "layout": "flat_table",
            "header_row": 1, "first_col": 1, "last_col": None, "header_checksum": None,
            "meta": {}, "last_read_at": now, "last_edit_time": now}


def _text(payload) -> str:
    return str(payload)


def test_свежесть_не_несёт_данных_людей():
    conn = FakeConn({key: _row(key) for key in
                     ("budget.live", "schedule.human", "schedule.workload", "schedule.test")},
                    read_at=datetime.now(UTC).isoformat())
    report = calendar_sync.freshness(conn)
    text = _text(report)
    assert all(mark not in text for mark in FORBIDDEN)


def test_свежесть_не_несёт_идентификаторов_файлов():
    """Адрес листа — не часть ответа о свежести: экрану он не нужен."""
    conn = FakeConn({"budget.live": _row("budget.live")})
    text = _text(calendar_sync.freshness(conn))
    assert "spreadsheet_id" not in text and "sheet_title" not in text


def test_сверка_календаря_не_несёт_имён_участников(monkeypatch):
    """В отчёте сверки — маркеры и id событий, а не списки людей."""
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")

    class FakeEvents:
        def list(self, **_kwargs):
            return _Request({"items": [{
                "id": "evt-1", "summary": "Репетиция", "status": "confirmed",
                "start": {"dateTime": "2026-12-19T18:00:00+00:00"},
                "end": {"dateTime": "2026-12-19T21:00:00+00:00"},
                "extendedProperties": {"private": {"svoi_event_id": "1",
                                                    "svoi_people": "Дарья Смирнова"}}}]})

        def delete(self, **_kwargs):
            raise AssertionError("dry_run не должен удалять события")

    class _Request:
        def __init__(self, answer):
            self.answer = answer

        def execute(self):
            return self.answer

    class FakeService:
        def events(self):
            return FakeEvents()

    conn = FakeConn()
    conn.fetchall = lambda: []          # type: ignore[method-assign]
    report = calendar_sync.scan(conn, read_service=FakeService(), draft_service=FakeService(),
                                time_from=datetime(2026, 12, 1, tzinfo=UTC),
                                time_to=datetime(2027, 1, 1, tzinfo=UTC), dry_run=True)
    text = _text(report)
    assert "Дарья" not in text and "Смирнова" not in text

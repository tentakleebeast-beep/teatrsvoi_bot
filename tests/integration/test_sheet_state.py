"""Признак «лист подтвердил запись» в budget_tx (C-3, INT-002)."""

import importlib

from tests.conftest import _test_dsn, google_error


def _payload(key: str) -> dict:
    return {"chat_id": 123456789, "tx_type": "expense", "amount": 1200,
            "category": "Реквизит и костюмы", "purpose": "сверка листа",
            "idempotency_key": key}


def _sheet_state(key: str) -> tuple[str | None, str | None]:
    import psycopg

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select sheet_written_at, sheet_error from budget_tx "
                           "where idempotency_key = %s", (key,)).fetchone()
    return (row[0], row[1])


class _FakeAppend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {}


class _FakeValues:
    def __init__(self, append: _FakeAppend) -> None:
        self.append_obj = append
        self.kwargs = None

    def append(self, **kwargs):
        self.kwargs = kwargs
        return self.append_obj


class _FakeSheets:
    def __init__(self, append: _FakeAppend) -> None:
        self.values_obj = _FakeValues(append)

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_obj


def test_сбой_записи_в_лист_помечается_в_бд(client, auth, monkeypatch):
    """Google ответил ошибкой: операция в БД, но лист её не подтвердил."""
    main = importlib.import_module("app.core")
    append = _FakeAppend(error=google_error(400, "quota exceeded"))
    monkeypatch.setattr(main, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(main, "_sheets", lambda: _FakeSheets(append))

    r = client.post("/tools/budget/commit", json=_payload("tg:sheet:fail"), headers=auth)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert append.calls == 1

    written_at, error = _sheet_state("tg:sheet:fail")
    assert written_at is None
    assert error and "quota" in error


def test_успешная_запись_в_лист_подтверждает_операцию(client, auth, monkeypatch):
    """Append прошёл: sheet_written_at заполнен, ошибки нет."""
    main = importlib.import_module("app.core")
    append = _FakeAppend()
    monkeypatch.setattr(main, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(main, "_sheets", lambda: _FakeSheets(append))

    r = client.post("/tools/budget/commit", json=_payload("tg:sheet:ok"), headers=auth)
    assert r.status_code == 200
    assert append.calls == 1

    written_at, error = _sheet_state("tg:sheet:ok")
    assert written_at is not None
    assert error is None

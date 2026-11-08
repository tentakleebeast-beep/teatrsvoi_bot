"""Повтор записи неподтверждённых строк в лист (C-3, Ш-26, INT-002)."""

import importlib

from tests.conftest import _test_dsn, google_error


def _payload(key: str) -> dict:
    return {"chat_id": 123456789, "tx_type": "expense", "amount": 1500,
            "category": "Реквизит и костюмы", "purpose": "повтор записи",
            "idempotency_key": key}


def _tx(key: str) -> tuple[int, str | None, str | None]:
    import psycopg

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select id, sheet_written_at, sheet_error from budget_tx "
                           "where idempotency_key = %s", (key,)).fetchone()
    return row[0], row[1], row[2]


class _FakeAppend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.rows: list[list] = []

    def execute(self):
        if self.error:
            raise self.error
        return {}


class _FakeGet:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def execute(self):
        return {"values": [[i] for i in self.ids]}


class _FakeValues:
    def __init__(self, append_obj: _FakeAppend, ids: list[str]) -> None:
        self.append_obj = append_obj
        self.ids = ids
        self.appended: list[list] = []

    def append(self, **kwargs):
        self.appended.append(kwargs["body"]["values"][0])
        return self.append_obj

    def get(self, **kwargs):
        return _FakeGet(self.ids)


class _FakeSheets:
    def __init__(self, append_obj: _FakeAppend, ids: list[str]) -> None:
        self.values_obj = _FakeValues(append_obj, ids)

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_obj


def _wire(monkeypatch, append_obj, ids=None):
    main = importlib.import_module("app.core")
    sheets = _FakeSheets(append_obj, ids or [])
    monkeypatch.setattr(main, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(main, "_sheets", lambda: sheets)
    return main, sheets


def test_сбой_затем_успешный_повтор_дописывает_строку(client, auth, monkeypatch):
    """Сбой append → повтор → строка в листе и признак подтверждения."""
    main, sheets = _wire(monkeypatch, _FakeAppend(error=google_error(400, "quota exceeded")))

    r = client.post("/tools/budget/commit", json=_payload("tg:retry:ok"), headers=auth)
    assert r.status_code == 200
    assert r.json()["sheet"] == "error"
    tx_id, written_at, error = _tx("tg:retry:ok")
    assert written_at is None and error and "quota" in error

    # лист ожил: повтор дописывает ту же операцию
    sheets.values_obj.append_obj = _FakeAppend()
    r = client.post("/tools/budget/sheet-retry", json={}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["written"] == [tx_id]
    assert body["failed"] == [] and body["remaining"] == 0
    assert sheets.values_obj.appended[-1][0] == str(tx_id)

    _, written_at, error = _tx("tg:retry:ok")
    assert written_at is not None and error is None

    # подтверждённая строка больше не попадает в повтор
    r = client.post("/tools/budget/sheet-retry", json={}, headers=auth)
    assert r.json()["attempted"] == 0


def test_повторный_сбой_обновляет_ошибку(client, auth, monkeypatch):
    """Лист снова ответил ошибкой: признака подтверждения нет, текст ошибки свежий."""
    _wire(monkeypatch, _FakeAppend(error=google_error(400, "first: timeout")))
    client.post("/tools/budget/commit", json=_payload("tg:retry:fail"), headers=auth)
    tx_id, _, _ = _tx("tg:retry:fail")

    main = importlib.import_module("app.core")
    sheets = _FakeSheets(_FakeAppend(error=google_error(400, "again: quota")), [])
    monkeypatch.setattr(main, "_sheets", lambda: sheets)
    r = client.post("/tools/budget/sheet-retry", json={}, headers=auth)
    body = r.json()
    assert body["written"] == []
    assert [row["id"] for row in body["failed"]] == [tx_id]
    assert "again: quota" in body["failed"][0]["error"]
    assert body["remaining"] == 1

    _, written_at, error = _tx("tg:retry:fail")
    assert written_at is None and "again" in error


def test_строка_уже_в_листе_не_дублируется(client, auth, monkeypatch):
    """Append прошёл, ответ потерялся: повтор подтверждает строку, но не пишет второй раз."""
    _wire(monkeypatch, _FakeAppend(error=google_error(400, "read timeout")))
    client.post("/tools/budget/commit", json=_payload("tg:retry:dupe"), headers=auth)
    tx_id, written_at, _ = _tx("tg:retry:dupe")
    assert written_at is None

    main = importlib.import_module("app.core")
    sheets = _FakeSheets(_FakeAppend(), ids=[str(tx_id)])
    monkeypatch.setattr(main, "_sheets", lambda: sheets)
    body = client.post("/tools/budget/sheet-retry", json={"limit": 10},
                       headers=auth).json()
    assert body["already"] == [tx_id]
    assert body["written"] == []
    assert sheets.values_obj.appended == []

    _, written_at, error = _tx("tg:retry:dupe")
    assert written_at is not None and error is None


def test_повтор_пишет_аудит(client, auth, monkeypatch):
    import psycopg

    _wire(monkeypatch, _FakeAppend(error=google_error(400, "quota")))
    client.post("/tools/budget/commit", json=_payload("tg:retry:audit"), headers=auth)
    main = importlib.import_module("app.core")
    monkeypatch.setattr(main, "_sheets", lambda: _FakeSheets(_FakeAppend(), []))
    client.post("/tools/budget/sheet-retry", json={}, headers=auth)

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select count(*) from audit_event "
                           "where action = 'budget.sheet.retry'").fetchone()
    assert row[0] >= 1


def test_осталось_считает_всю_очередь_а_не_порцию(client, auth, monkeypatch):
    """При limit меньше очереди «осталось» — число по всей базе, а не по порции."""
    _wire(monkeypatch, _FakeAppend(error=google_error(400, "quota")))
    client.post("/tools/budget/commit", json=_payload("tg:retry:batch1"), headers=auth)
    client.post("/tools/budget/commit", json=_payload("tg:retry:batch2"), headers=auth)

    main = importlib.import_module("app.core")
    monkeypatch.setattr(main, "_sheets",
                        lambda: _FakeSheets(_FakeAppend(error=google_error(400, "quota")), []))
    body = client.post("/tools/budget/sheet-retry", json={"limit": 1}, headers=auth).json()
    assert body["attempted"] == 1
    assert len(body["failed"]) == 1
    assert body["remaining"] == 2, "осталось должно считать очередь целиком, а не порцию"

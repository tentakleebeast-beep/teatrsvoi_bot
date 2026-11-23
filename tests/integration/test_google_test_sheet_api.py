"""Маршрут репетиции цветов `POST /tools/availability/test-sheet` (186 AC-10).

Обоснование: docs/development/186 §4.3, AC-10, §13.4. Маршрут закрыт существующей
возможностью `admin.panel`: репетиция раскладки — решение администратора, а не действие
участника над своей занятостью. Тело проверяется до записи: час вне 08:00…23:00 и
месяц не в формате — отказ, а не «первая страница файла».
"""

from __future__ import annotations

import psycopg
import pytest
from app import core, schedule_test_sheet
from app.person_key import make_key

from tests.conftest import _test_dsn


def _person(conn, chat_id: int, name: str, role: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _headers(chat_id: int, token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Actor-Chat-Id": str(chat_id)}


@pytest.fixture
def write_calls(monkeypatch):
    """Подмена записи: маршрут проверяется без Google и без живого тестового листа."""
    calls: list[dict] = []

    def fake(conn, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "sheet": "11.2026 · Дарья", "applied": kwargs.get("apply", True),
                "verified": True, "written": 1, "skipped": 0, "cells": []}

    monkeypatch.setattr(schedule_test_sheet, "write_probes", fake)
    return calls


def test_администратор_пишет_цветовую_пробу(client, write_calls):
    """Админ с `admin.panel`: проба доходит до протокола с месяцем, ником и часом."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 556001, "Цветной Админ", "admin")

    answer = client.post("/tools/availability/test-sheet",
                         headers=_headers(556001, TOKEN),
                         json={"month": "2026-11", "nick": "Дарья", "date": "2026-11-12",
                               "hour": 10, "status": "busy", "apply": True})
    assert answer.status_code == 200, answer.text
    assert answer.json()["sheet"] == "11.2026 · Дарья"
    assert write_calls and write_calls[0]["month"] == "2026-11"
    assert write_calls[0]["nick"] == "Дарья"
    assert write_calls[0]["probes"][0].hour == 10


def test_участник_без_права_не_пишет(client, write_calls):
    """У труппы `admin.panel` нет — 403, протокол записи не вызывается."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 556002, "Цветной Артист", "troupe")

    answer = client.post("/tools/availability/test-sheet",
                         headers=_headers(556002, TOKEN),
                         json={"month": "2026-11", "nick": "Дарья", "date": "2026-11-12",
                               "hour": 10, "status": "busy"})
    assert answer.status_code == 403, answer.text
    assert write_calls == []


def test_час_вне_раскладки_отклоняется_до_записи(client, write_calls):
    """08:00…23:00 — границы раскладки: 7 и 24 не доходят до протокола."""
    from tests.conftest import TOKEN

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 556003, "Цветной Админ 2", "admin")

    for hour in (7, 24):
        answer = client.post("/tools/availability/test-sheet",
                             headers=_headers(556003, TOKEN),
                             json={"month": "2026-11", "nick": "Дарья",
                                   "date": "2026-11-12", "hour": hour})
        assert answer.status_code == 422, answer.text
    assert write_calls == []


class _Call:
    """Ответ Google в форме `.execute()` — как у настоящего клиента."""

    def __init__(self, value: dict) -> None:
        self.value = value

    def execute(self) -> dict:
        return self.value


class _Values:
    def __init__(self, grid: list[list]) -> None:
        self.grid = grid
        self.ranges: list[str] = []

    def get(self, spreadsheetId, range, majorDimension=None):  # noqa: A002 — имена полей API
        self.ranges.append(range)
        if range.endswith(":AZ1"):
            return _Call({"values": [self.grid[0]]})
        return _Call({"values": self.grid})


class _Spreadsheets:
    def __init__(self, titles: tuple[str, ...], grid: list[list]) -> None:
        self.titles = titles
        self.values_api = _Values(grid)

    def values(self) -> _Values:
        return self.values_api

    def get(self, spreadsheetId, fields=None):  # noqa: A002
        return _Call({"sheets": [{"properties": {"sheetId": 7, "title": title}}
                                 for title in self.titles],
                      "properties": {"modifiedTime": "2026-11-01T10:00:00Z"}})


class _Sheets:
    def __init__(self, titles: tuple[str, ...], grid: list[list]) -> None:
        self.spreadsheets_api = _Spreadsheets(titles, grid)

    def spreadsheets(self) -> _Spreadsheets:
        return self.spreadsheets_api


def test_пустая_настройка_шаблона_пишет_страницу_по_умолчанию(client, monkeypatch):
    """Дефолт `SCHEDULE_TEST_SHEET_PATTERN=''` — страница `MM.YYYY · Ник`, а не отказ.

    Владелец задаёт только id файла; маршрут обязан дойти до протокола записи на
    стандартной странице (186 §2.2, AC-10), а не отвечать `sheet_ambiguous` всегда.
    """
    from tests.conftest import TOKEN

    page = "11.2026 · Дарья"
    grid = [["Час", "01.11.2026", "02.11.2026"], ["08:00", "", ""], ["09:00", "", ""]]
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "test-test-sheet")
    monkeypatch.setattr(core, "SCHEDULE_TEST_SHEET_PATTERN", "")
    monkeypatch.setattr(core, "_sheets", lambda: _Sheets((page,), grid))

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 556004, "Цветной Админ 3", "admin")

    answer = client.post("/tools/availability/test-sheet",
                         headers=_headers(556004, TOKEN),
                         json={"month": "2026-11", "nick": "Дарья", "date": "2026-11-02",
                               "hour": 9, "status": "busy", "apply": False})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["sheet"] == page
    assert body["cells"] and body["cells"][0]["cell"] == "C3"

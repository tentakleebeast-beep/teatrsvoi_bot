"""Запись занятости в лист через API (FR-C9) на поддельном Sheets.

Google не вызываем: проверяем то, что решает бот — план, запись, проверку фактом
и политику «правку человека не затираем без force».
"""

import datetime as dt

import psycopg
import pytest
from app.person_key import make_key
from app.schedule_sheet import cell_a1, column_index

from tests.conftest import _test_dsn, service_today

TITLE = "студийцы"
HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "793001"}


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeSheets:
    """Минимальная подделка googleapiclient: get/batchUpdate по A1-адресам."""

    def __init__(self, grid: list[list]):
        self.grid = [list(row) for row in grid]
        self.writes = 0
        self.hidden = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def _set(self, a1: str, text: str) -> None:
        letters = "".join(ch for ch in a1 if ch.isalpha())
        row = int("".join(ch for ch in a1 if ch.isdigit()))
        column = column_index(letters)
        while len(self.grid) < row:
            self.grid.append([])
        line = self.grid[row - 1]
        while len(line) < column:
            line.append("")
        line[column - 1] = text

    def _slice(self, a1_range: str) -> list[list]:
        """Как настоящий API: get возвращает кусок начиная с первого адреса диапазона."""
        box = a1_range.split("!")[-1]
        first, _, last = box.partition(":")
        last = last or first

        def parts(cell):
            letters = "".join(ch for ch in cell if ch.isalpha())
            row = int("".join(ch for ch in cell if ch.isdigit()))
            return row, column_index(letters)

        row_from, col_from = parts(first)
        row_to, col_to = parts(last)
        out = []
        for row in range(row_from, row_to + 1):
            line = self.grid[row - 1] if row - 1 < len(self.grid) else []
            piece = [line[c - 1] if c - 1 < len(line) else ""
                     for c in range(col_from, col_to + 1)]
            while piece and not str(piece[-1]).strip():      # хвост API не отдаёт
                piece.pop()
            out.append([str(cell) for cell in piece])
        while out and not out[-1]:
            out.pop()
        return out

    def batchGet(self, spreadsheetId=None, ranges=None):
        return _Req(lambda: {"valueRanges": [{"range": r, "values": self._slice(r)}
                                             for r in (ranges or [])]})

    def get(self, spreadsheetId=None, range=None, fields=None, majorDimension=None):
        if fields and "sheets(" in fields:
            return _Req(lambda: {"sheets": [{"properties": {"sheetId": 7, "title": TITLE}}]})
        return _Req(lambda: {"values": self._slice(range or "A1")})

    def batchUpdate(self, spreadsheetId=None, body=None):
        if body and "requests" in body:
            for request in body["requests"]:
                self.hidden.append(request["updateDimensionProperties"]["range"]["startIndex"] + 1)
            return _Req(lambda: {})
        for item in (body or {}).get("data", []):
            self._set(item["range"].split("!")[-1], item["values"][0][0])
        self.writes += 1
        return _Req(lambda: {})


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _grid(monday: dt.date) -> list[list]:
    grid = [["", "", "Решение", "Дарья Смирнова"]]
    grid.append(["", "ноябрь", "", ""])
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        grid.append([["пн", "вт", "ср", "чт", "пт", "сб", "вс"][offset],
                     day.strftime("%d.%m.%y"), "", ""])
    return grid


def _setup(monkeypatch, client, chat_id: int = 793001):
    # доступ к листу и настройки листа живут в ядре (этап B, карта t_135f65cd)
    from app import core

    monday = service_today()
    monday -= dt.timedelta(days=monday.weekday())
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat_id, "Дарья Смирнова")
    fake = FakeSheets(_grid(monday))
    monkeypatch.setattr(core, "_sheets", lambda: fake)
    monkeypatch.setattr(core, "SCHEDULE_SPREADSHEET_ID", "fake")
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", TITLE)
    headers = {**HEADERS, "X-Actor-Chat-Id": str(chat_id)}
    client.post("/tools/availability/week", headers=headers,
                json={"start": monday.isoformat(),
                      "days": [{"day": monday.isoformat(), "status": "busy",
                                "to_time": "18:30"},
                               {"day": (monday + dt.timedelta(days=1)).isoformat(),
                                "status": "free", "from_time": "19:00"}]})
    return fake, monday, headers


def test_fr_c9_предпросмотр_ничего_не_пишет(monkeypatch, client):
    """FR-C9: без apply лист не трогаем — только план и адреса."""
    fake, monday, headers = _setup(monkeypatch, client)
    body = client.post("/tools/availability/sheet", headers=headers,
                       json={"start": monday.isoformat()}).json()
    assert body["ok"] is True and body["applied"] is False
    assert body["person_column"] == 4
    assert {c["cell"] for c in body["cells"]} >= {cell_a1(3, 4), cell_a1(4, 4)}
    assert fake.writes == 0


def test_fr_c9_запись_проверяется_чтением_и_прячет_служебную_колонку(monkeypatch, client):
    """FR-C9: пишем канон, перечитываем адреса и скрываем колонку снимка."""
    fake, monday, headers = _setup(monkeypatch, client)
    body = client.post("/tools/availability/sheet", headers=headers,
                       json={"start": monday.isoformat(), "apply": True}).json()
    assert body["applied"] is True and body["verified"] is True, body
    assert fake.grid[2][3] == "занят до 18:30"
    assert fake.grid[3][3] == "свободен с 19:00"
    assert fake.hidden and fake.grid[0][fake.hidden[0] - 1] == "svoi"


def test_fr_c9_правку_человека_не_затираем_без_force(monkeypatch, client):
    """FR-C9: клетка разошлась со снимком — запись отказывает, пока не сказано force."""
    fake, monday, headers = _setup(monkeypatch, client)
    client.post("/tools/availability/sheet", headers=headers,
                json={"start": monday.isoformat(), "apply": True})
    fake._set(cell_a1(3, 4), "свободно с 20:00")          # человек поправил руками

    refused = client.post("/tools/availability/sheet", headers=headers,
                          json={"start": monday.isoformat(), "apply": True}).json()
    assert refused["ok"] is False and refused["error"] == "mismatches_present"
    assert refused["mismatches"][0]["cell"] == "свободно с 20:00"
    assert fake.grid[2][3] == "свободно с 20:00"            # правка на месте

    forced = client.post("/tools/availability/sheet", headers=headers,
                         json={"start": monday.isoformat(), "apply": True, "force": True}).json()
    assert forced["applied"] is True and forced["verified"] is True
    assert fake.grid[2][3] == "занята до 18:30" or fake.grid[2][3] == "занят до 18:30"


def _occupancy(pid: int, day: dt.date) -> dict[int, str]:
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select hour, kind, source from availability_hour "
            "where participant_id = (select id from participant where tg_chat_id = %s) and day = %s "
            "order by hour", (pid, day)).fetchall()
    return {row[0]: row[1] for row in rows}


def test_fr_c9_сверка_переносит_правку_человека_в_базу(monkeypatch, client):
    """FR-C9, FR-G13: файл — истина, но правка переносится по подтверждению, а не молча."""
    fake, monday, headers = _setup(monkeypatch, client)
    client.post("/tools/availability/sheet", headers=headers,
                json={"start": monday.isoformat(), "apply": True})
    fake._set(cell_a1(3, 4), "свободна с 20:00")        # человек поправил день

    preview = client.post("/tools/availability/reconcile", headers=headers,
                          json={"start": monday.isoformat()}).json()
    assert preview["applied"] is False
    assert preview["items"][0]["action"] == "принять"
    assert preview["items"][0]["cell"] == "свободна с 20:00"
    # канон пишется по роду из профиля (в тесте профиль мужской) — это одна норма на всех
    assert preview["items"][0]["new"] == "свободен с 20:00"
    assert _occupancy(793001, monday)[18] == "busy"     # до подтверждения база не тронута

    refused = client.post("/tools/availability/reconcile", headers=headers,
                          json={"start": monday.isoformat(), "apply": True}).json()
    assert refused["ok"] is False and refused["error"] == "accept_required"

    done = client.post("/tools/availability/reconcile", headers=headers,
                       json={"start": monday.isoformat(), "apply": True, "accept": True}).json()
    assert done["applied"] is True and done["problems"] == []
    hours = _occupancy(793001, monday)
    assert hours[19] == "busy" and hours[20] == "free"   # свободна с 20:00
    assert hours[17] == "busy"

    again = client.get("/tools/availability/sheet/mismatches", headers=headers,
                       params={"start": monday.isoformat()}).json()
    assert again["mismatches"] == []                     # снимок обновлён, шума нет


def test_fr_c9_пустая_клетка_стирает_день(monkeypatch, client):
    """FR-C9: человек стёр клетку — значит данных нет, а не «свободен по умолчанию»."""
    fake, monday, headers = _setup(monkeypatch, client)
    client.post("/tools/availability/sheet", headers=headers,
                json={"start": monday.isoformat(), "apply": True})
    fake._set(cell_a1(3, 4), "")

    preview = client.post("/tools/availability/reconcile", headers=headers,
                          json={"start": monday.isoformat()}).json()
    assert preview["items"][0]["action"] == "стереть"

    client.post("/tools/availability/reconcile", headers=headers,
                json={"start": monday.isoformat(), "apply": True, "accept": True})
    assert _occupancy(793001, monday) == {}


def test_fr_c9_неразобранный_текст_не_угадывается(monkeypatch, client):
    """FR-C9: непонятная запись остаётся человеку, а не превращается в догадку бота."""
    fake, monday, headers = _setup(monkeypatch, client)
    client.post("/tools/availability/sheet", headers=headers,
                json={"start": monday.isoformat(), "apply": True})
    fake._set(cell_a1(3, 4), "абракадабра")

    done = client.post("/tools/availability/reconcile", headers=headers,
                       json={"start": monday.isoformat(), "apply": True, "accept": True}).json()
    assert done["applied"] is True
    assert done["problems"] and done["problems"][0]["reason"] == "текст не разобран"
    assert done["accepted"] == []
    assert _occupancy(793001, monday)[18] == "busy"      # база не изменилась

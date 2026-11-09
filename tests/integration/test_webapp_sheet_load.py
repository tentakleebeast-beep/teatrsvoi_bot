"""
Загрузка своей колонки из общего листа через API (Э5, FR-C9, docs/development/149).

Здесь проверяется то, что видит человек и что остаётся в базе: предпросмотр ничего не
пишет, загрузка меняет только выбранные дни и делает это одной транзакцией, в лист не
уходит ни одной записи, а источник отказа не превращается в догадку.

Google не вызываем: `FakeSheet` умеет ровно `values().get` и падает на любой попытке
записи — так требование «источник только читаем» (149 D2) проверяется исполнением,
а не чтением кода.

Обоснование: FR-C9, docs/development/149 §4.4, §8 (К2–К13).
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from app.person_key import make_key
from fastapi.testclient import TestClient

from tests.conftest import _test_dsn, google_error, service_today
from tests.integration.test_webapp_profile import _api, init_data

TAB = "студийцы"
CHAT = 794001
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeSheet:
    """Только чтение значений листа: любая запись — провал теста, а не тихий вызов."""

    def __init__(self, values: list[list]):
        self.values_ = values
        self.reads: list[str] = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, fields=None, majorDimension=None):
        self.reads.append(range or "")
        return _Req(lambda: {"values": self.values_})

    def __getattr__(self, name: str):
        raise AssertionError(f"в лист нельзя: {name}")


def _monday() -> dt.date:
    today = service_today()
    return today - dt.timedelta(days=today.weekday())


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _token(client, chat_id: int = CHAT) -> dict:
    _api()
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _grid(monday: dt.date, cells: dict[int, str], names: list[str]) -> list[list]:
    """Живой расклад: шапка, блок месяца, дни в колонке B, отметки в колонке D.

    Как в листе «Расписание студийцы»: A — день недели, B — дата, C — «Решение»,
    с D идут люди. Отметки пишем в колонку первого человека — D.
    """
    values = [["", "", "Решение", *names]]
    values.append(["", "ноябрь", "", ""])
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        values.append([WEEKDAYS[offset], day.strftime("%d.%m.%y"), "",
                       cells.get(offset, "")])
    return values


def _setup(monkeypatch, client, cells: dict[int, str], name: str = "Дарья Смирнова", *,
           sheet_name: str | None = None, names: list[str] | None = None,
           chat_id: int = CHAT):
    from app import core

    _api()
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, chat_id, name)
    fake = FakeSheet(_grid(monday, cells, names or [sheet_name or name]))
    monkeypatch.setattr(core, "_sheets", lambda: fake)
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "user-sheet")
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SHEET_TITLE", TAB)
    return fake, monday, _token(client, chat_id), pid


def _save_week(client, headers, monday: dt.date, days: list[tuple[int, str]]) -> None:
    """Отметить дни в базе обычным путём недели — с каноническим текстом в `raw_text`."""
    body = {"start": monday.isoformat(),
            "days": [{"day": (monday + dt.timedelta(days=offset)).isoformat(), "status": status}
                     for offset, status in days]}
    assert client.post("/webapp/availability/week", headers=headers, json=body).json()["ok"]


def _hours(pid: int, day: dt.date) -> dict[int, tuple[str, str, str]]:
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select hour, kind, source, raw_text from availability_hour "
            "where participant_id = %s and day = %s order by hour", (pid, day)).fetchall()
    return {row[0]: (row[1], row[2], row[3] or "") for row in rows}


def _preview(client, headers, monday: dt.date) -> dict:
    response = client.get("/webapp/availability/sheet/preview", headers=headers,
                          params={"start": monday.isoformat()})
    return response


def test_э5_предпросмотр_отдаёт_дни_и_ничего_не_пишет(monkeypatch, client):
    """К2: совпавшие дни не показываются, счётчики названы, источник — общий лист."""
    cells = {0: "свободен", 1: "занят", 2: "занят", 3: "как получится"}
    fake, monday, headers, pid = _setup(monkeypatch, client, cells)
    _save_week(client, headers, monday,
               [(0, "free"), (1, "free"), (2, "free"), (3, "free")])
    before = {offset: _hours(pid, monday + dt.timedelta(days=offset)) for offset in range(7)}

    r = _preview(client, headers, monday)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["start"] == monday.isoformat()
    assert body["source"] == {"spreadsheet": "Расписание студийцы", "tab": TAB}
    assert body["person"]["column"] == "D"
    days = {item["day"]: item for item in body["days"]}
    first = (monday + dt.timedelta(days=1)).isoformat()
    assert days[monday.isoformat()]["state"] == "same"
    assert days[first]["state"] == "differs"
    assert days[(monday + dt.timedelta(days=3)).isoformat()]["state"] == "unparsed"
    assert body["counts"] == {"same": 1, "new": 0, "differs": 2,
                              "sheet_empty": 0, "unparsed": 1}
    assert fake.reads == [f"'{TAB}'!A1:AZ3000"], "читаем только значения, и весь лист"
    after = {offset: _hours(pid, monday + dt.timedelta(days=offset)) for offset in range(7)}
    assert after == before, "предпросмотр не пишет в базу"


def test_э5_загрузка_меняет_только_выбранные_дни(monkeypatch, client):
    """К5: загрузились ровно выбранные дни, остальные строки не тронуты."""
    cells = {0: "свободен", 1: "занят", 2: "свободен"}
    fake, monday, headers, pid = _setup(monkeypatch, client, cells)
    _save_week(client, headers, monday, [(0, "free"), (1, "free"), (2, "busy")])
    before = {offset: _hours(pid, monday + dt.timedelta(days=offset)) for offset in range(7)}

    monday_day = monday
    tuesday = monday + dt.timedelta(days=1)
    r = client.post("/webapp/availability/sheet/load", headers=headers,
                    json={"start": monday.isoformat(),
                          "days": [monday_day.isoformat(), tuesday.isoformat()]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["applied"] is True
    assert body["days"] == [{"day": tuesday.isoformat(), "action": "загружен",
                             "text": "занят"}]
    assert body["skipped"] == [{"day": monday_day.isoformat(), "reason": "same"}]

    loaded = _hours(pid, tuesday)
    assert loaded and {kind for kind, _, _ in loaded.values()} == {"busy"}
    assert {source for _, source, _ in loaded.values()} == {"sheet_import"}
    assert {text for _, _, text in loaded.values()} == {"занят"}

    for offset in (0, 2, 3, 4, 5, 6):
        day = monday + dt.timedelta(days=offset)
        assert _hours(pid, day) == before[offset], f"день {day} меняться не должен"


def test_э5_пустая_клетка_стирает_день_только_по_выбору(monkeypatch, client):
    """К4: в листе пусто, в базе «занят весь день» — день стирается, если его включили."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {})
    _save_week(client, headers, monday, [(0, "busy")])
    assert _hours(pid, monday)

    body = _preview(client, headers, monday).json()
    assert body["days"][0]["state"] == "sheet_empty", "по умолчанию день выключен"

    done = client.post("/webapp/availability/sheet/load", headers=headers,
                       json={"start": monday.isoformat(), "days": [monday.isoformat()]}).json()
    assert done["ok"] is True
    assert done["days"] == [{"day": monday.isoformat(), "action": "стёрт", "text": ""}]
    assert _hours(pid, monday) == {}


def test_э5_вопрос_грузится_как_неуверенность(monkeypatch, client):
    """К7: «?» — это `uncertain`, а не «свободен» и не «не разобрано»."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "?"})
    done = client.post("/webapp/availability/sheet/load", headers=headers,
                       json={"start": monday.isoformat(), "days": [monday.isoformat()]}).json()
    assert done["ok"] is True
    hours = _hours(pid, monday)
    assert {kind for kind, _, _ in hours.values()} == {"uncertain"}
    assert {text for _, _, text in hours.values()} == {"?"}, "в базе текст листа как написан"

    week = client.get("/webapp/availability/week", headers=headers,
                      params={"start": monday.isoformat()}).json()
    assert week["days"][0]["status"] == "uncertain"
    assert week["days"][0]["text"] == "пока не понятно"


def test_э5_журнал_помнит_дни_тексты_и_колонку(monkeypatch, client):
    """К12: одно событие на загрузку, в нём — до и после по каждому дню и адрес колонки."""
    cells = {0: "свободен", 1: "занят"}
    fake, monday, headers, pid = _setup(monkeypatch, client, cells)
    _save_week(client, headers, monday, [(0, "busy")])
    client.post("/webapp/availability/sheet/load", headers=headers,
                json={"start": monday.isoformat(), "days": [monday.isoformat()]})

    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select actor_id, entity_type, entity_id, before, after, source from audit_event "
            "where action = 'availability.sheet.pull' order by id desc").fetchall()
    assert len(rows) == 1
    actor_id, entity_type, entity_id, before, after, source = rows[0]
    assert actor_id == pid and entity_type == "availability_hour"
    assert entity_id == f"{pid}:{monday.isoformat()}"
    assert before == {monday.isoformat(): {"state": "busy", "text": "занят"}}
    assert after == {monday.isoformat(): {"state": "free", "text": "свободен"}}
    assert source == {"spreadsheet": "Расписание студийцы", "tab": TAB, "column": "D"}


def test_э5_нечего_грузить_когда_всё_совпало(monkeypatch, client):
    """К11: после загрузки повторная попытка честно отвечает «загружать нечего»."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "свободен"})
    _save_week(client, headers, monday, [(0, "free")])
    body = client.post("/webapp/availability/sheet/load", headers=headers,
                       json={"start": monday.isoformat(), "days": [monday.isoformat()]}).json()
    assert body["ok"] is False and body["error"] == "nothing_to_write"
    assert body["skipped"] == [{"day": monday.isoformat(), "reason": "same"}]
    assert "detail" in body


def test_э5_лист_недоступен_и_база_не_тронута(monkeypatch, client):
    """К10: отказ Google — понятный код и текст, в базе ничего не меняется."""
    from app import core

    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "занят"})
    _save_week(client, headers, monday, [(0, "free")])
    before = _hours(pid, monday)

    def broken():
        raise google_error(503)

    monkeypatch.setattr(core, "_sheets", broken)
    preview = _preview(client, headers, monday).json()
    assert preview["ok"] is False and preview["error"] == "sheet_unavailable"
    assert "недоступен" in preview["detail"]

    load = client.post("/webapp/availability/sheet/load", headers=headers,
                       json={"start": monday.isoformat(), "days": [monday.isoformat()]}).json()
    assert load["ok"] is False and load["error"] == "sheet_unavailable"
    assert _hours(pid, monday) == before


def test_э5_без_настройки_листа_отказ_а_не_падение(monkeypatch, client):
    """К10: пустой адрес листа — `no_spreadsheet`, а не обращение к пустому id."""
    from app import core

    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "занят"})
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "")
    body = _preview(client, headers, monday).json()
    assert body["ok"] is False and body["error"] == "no_spreadsheet"


def test_э5_человека_нет_в_листе_загрузки_нет(monkeypatch, client):
    """К8: нет колонки — отказ с текстом, списка дней нет, загрузка не отправляется."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {}, name="Сторожук Юлия",
                                       sheet_name="Дарья Смирнова")
    body = _preview(client, headers, monday).json()
    assert body["ok"] is False and body["error"] == "person_not_found"
    assert "колонки с вашим именем" in body["detail"]
    assert "days" not in body


def test_э5_две_колонки_это_отказ_с_буквами(monkeypatch, client):
    """К9: две колонки с одним именем — отказ и буквы, а не запись в первую попавшуюся."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {},
                                       names=["Дарья Смирнова", "Смирнова Дарья"])
    body = _preview(client, headers, monday).json()
    assert body["ok"] is False and body["error"] == "person_ambiguous"
    assert body["columns"] == ["D", "E"]
    assert "D, E" in body["detail"]


def test_э5_псевдоним_из_базы_находит_колонку(monkeypatch, client):
    """К9: короткое имя в шапке — псевдоним из `person_alias`, ролевой псевдоним не в счёт."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "свободен"},
                                       name="Виктор Мамлютов", names=["Витя"])
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into person_alias (participant_id, alias, alias_kind, source)"
                     " values (%s, 'витя', 'short', 'manual')", (pid,))
        conn.execute("insert into person_alias (participant_id, alias, alias_kind, source)"
                     " values (%s, 'мастер', 'role', 'manual')", (pid,))
        conn.commit()
    body = _preview(client, headers, monday).json()
    assert body["ok"] is True and body["person"]["column"] == "D"

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from person_alias where participant_id = %s", (pid,))
        conn.execute("insert into person_alias (participant_id, alias, alias_kind, source)"
                     " values (%s, 'мастер', 'role', 'manual')", (pid,))
        conn.commit()
    from app import core

    core_sheet = FakeSheet(_grid(monday, {}, ["Мастер"]))
    monkeypatch.setattr(core, "_sheets", lambda: core_sheet)
    role_only = _preview(client, headers, monday).json()
    assert role_only["ok"] is False and role_only["error"] == "person_not_found"


def test_э5_день_вне_недели_и_пустой_выбор_отклоняются_до_листа(monkeypatch, client):
    """§4.4: лишнюю дату и пустой выбор отбиваем, не читая лист и не трогая базу."""
    fake, monday, headers, pid = _setup(monkeypatch, client, {0: "занят"})

    outside = client.post("/webapp/availability/sheet/load", headers=headers,
                          json={"start": monday.isoformat(),
                                "days": [(monday + dt.timedelta(days=7)).isoformat()]})
    assert outside.status_code == 400 and outside.json()["code"] == "day_outside_week"

    empty = client.post("/webapp/availability/sheet/load", headers=headers,
                        json={"start": monday.isoformat(), "days": []})
    assert empty.status_code == 400 and empty.json()["code"] == "empty_selection"
    assert fake.reads == [], "отклонённый запрос до листа не доходит"

    wrong = client.get("/webapp/availability/sheet/preview", headers=headers,
                       params={"start": "не дата"})
    assert wrong.status_code == 400
    assert fake.reads == [], "кривая дата тоже не повод читать лист"


def test_э5_отказ_на_втором_дне_откатывает_загрузку(monkeypatch, client):
    """К15: падение на втором дне оставляет базу как была — частичной загрузки нет."""
    from app.api import schedule as schedule_api

    cells = {0: "занят", 1: "занят"}
    fake, monday, headers, pid = _setup(monkeypatch, client, cells)
    _save_week(client, headers, monday, [(0, "free"), (1, "free")])
    before = {offset: _hours(pid, monday + dt.timedelta(days=offset)) for offset in (0, 1)}

    real = schedule_api.availability_store.write_hours
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise psycopg.OperationalError("хранилище отказало")
        return real(*args, **kwargs)

    monkeypatch.setattr(schedule_api.availability_store, "write_hours", flaky)
    probe = TestClient(_app(), base_url="https://testserver", raise_server_exceptions=False)
    response = probe.post("/webapp/availability/sheet/load", headers=headers,
                          json={"start": monday.isoformat(),
                                "days": [monday.isoformat(),
                                         (monday + dt.timedelta(days=1)).isoformat()]})
    assert response.status_code == 500
    assert response.json()["ok"] is False

    monkeypatch.undo()
    for offset in (0, 1):
        assert _hours(pid, monday + dt.timedelta(days=offset)) == before[offset]


def _app():
    from app.main import app

    return app


def test_э5_в_лист_не_уходит_ни_одной_записи(monkeypatch, client):
    """К13: и предпросмотр, и загрузка обходятся одним `values.get` — FakeSheet не даст соврать."""
    cells = {0: "свободен", 1: "занят"}
    fake, monday, headers, pid = _setup(monkeypatch, client, cells)
    _preview(client, headers, monday)
    client.post("/webapp/availability/sheet/load", headers=headers,
                json={"start": monday.isoformat(), "days": [monday.isoformat()]})
    assert fake.reads == [f"'{TAB}'!A1:AZ3000"] * 2

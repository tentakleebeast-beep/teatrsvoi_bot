"""Сетка месяца «люди × дни» через API: лист, база и расхождения (FR-H9, З9).

Обоснование: FR-H9, docs/reports/124 §3 (З9), план 126 Ш-04, docs/development/48 §3.
Дефект, который сторожат тесты: месяц читался и правился из базы, а
Google-таблица — источник истины — на экран не попадала вовсе.

Google не вызываем: лист подменяется подделкой из `test_availability_sheet`
(те же `get`/`batchUpdate`, что у googleapiclient). Проверяется то, что решает
код: слова состояний, показ расхождения и отсутствие записи.
"""

import datetime as dt

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_availability_sheet import TITLE, FakeSheets
from tests.integration.test_webapp_profile import _api, init_data

MONTH = "2026-11"


def _person(conn, chat_id: int, name: str, role: str = "org") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)", (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _mark(participant_id: int, day: str, kind: str = "busy") -> None:
    with psycopg.connect(_test_dsn()) as conn:
        for hour in range(8, 24):
            conn.execute("""
                insert into availability_hour
                  (participant_id, day, hour, is_free, kind, source)
                values (%s,%s,%s,%s,%s,'bot')""",
                         (participant_id, day, hour, kind == "free", kind))
        conn.commit()


def _sheet(days: dict[dt.date, str]) -> list[list]:
    """Лист как у людей: шапка с именами, даты в колонке B, месяцы блоками."""
    values = [["", "", "Дарья Смирнова", "Сэм Второй"]]
    first = dt.date(2026, 11, 1)
    for offset in range(30):
        day = first + dt.timedelta(days=offset)
        values.append(["", day.strftime("%d.%m.%y"), days.get(day, ""), ""])
    return values


def _fake_sheet(monkeypatch, days: dict[dt.date, str]) -> FakeSheets:
    from app import core

    fake = FakeSheets(_sheet(days))
    monkeypatch.setattr(core, "_sheets", lambda: fake)
    monkeypatch.setattr(core, "SCHEDULE_SPREADSHEET_ID", "fake")
    monkeypatch.setattr(core, "SCHEDULE_SHEET_TITLE", TITLE)
    return fake


def test_организатор_видит_месяц_из_таблицы_и_расхождение_с_базой(client, monkeypatch):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781201, "Дарья Смирнова")
        _person(conn, 781202, "Сэм Второй")
    _mark(pid, "2026-11-10", "busy")       # база: занят — как и в листе
    _mark(pid, "2026-11-11", "busy")       # база: занят, а в листе «свободен»
    fake = _fake_sheet(monkeypatch, {dt.date(2026, 11, 10): "занят до 18:30",
                                     dt.date(2026, 11, 11): "свободен"})
    headers = _token(client, 781201)

    answer = client.get(f"/webapp/month-grid?month={MONTH}", headers=headers)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ok"] is True and body["month"] == MONTH
    assert len(body["days"]) == 30 and body["days"][0] == "2026-11-01"
    names = [person["name"] for person in body["people"]]
    assert "Дарья Смирнова" in names and "Сэм Второй" in names

    ok_cell = body["cells"][str(pid)]["2026-11-10"]
    assert ok_cell["word"] == "занят" and ok_cell["diff"] is False
    diff_cell = body["cells"][str(pid)]["2026-11-11"]
    assert diff_cell["word"] == "свободен" and diff_cell["base_word"] == "занят"
    assert diff_cell["diff"] is True

    assert [item["day"] for item in body["diffs"]] == ["2026-11-11"]
    assert "в таблице «свободен»" in body["diffs"][0]["text"]
    assert fake.writes == 0, "экран месяца ничего не записал в лист"


def test_участник_без_права_месяц_не_видит(client, monkeypatch):
    """Сетка читает всю труппу — значит право `schedule.read.all`, отказ 403."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781203, "Месяц Участник", role="troupe")
    _fake_sheet(monkeypatch, {})

    answer = client.get(f"/webapp/month-grid?month={MONTH}", headers=_token(client, 781203))
    assert answer.status_code == 403, answer.text
    assert "schedule.read.all" in answer.json()["detail"]


def test_лист_недоступен_показываем_базу(client, monkeypatch):
    """Google подвёл — экран не пустой и не врёт: показывает базу и говорит об этом."""
    from app import core

    def broken():
        raise OSError("нет связи с Google")

    monkeypatch.setattr(core, "_sheets", broken)
    monkeypatch.setattr(core, "SCHEDULE_SPREADSHEET_ID", "fake")
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781204, "Лист Недоступен")
    _mark(pid, "2026-11-12", "free")

    answer = client.get(f"/webapp/month-grid?month={MONTH}", headers=_token(client, 781204))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["sheet"]["available"] is False
    assert body["cells"][str(pid)]["2026-11-12"]["state"] == "free"
    assert "показана база" in body["note"]


def test_плохой_месяц_это_400(client, monkeypatch):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781205, "Месяц Ошибка")
    _fake_sheet(monkeypatch, {})

    answer = client.get("/webapp/month-grid?month=2026-13", headers=_token(client, 781205))
    assert answer.status_code == 400
    assert answer.json()["detail"] == "bad_month"

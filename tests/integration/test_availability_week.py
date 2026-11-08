"""Неделя занятости карточками дня через API (FR-C9).

Проверяем то, из-за чего этот контракт вообще нужен: запись недели одним вызовом,
чтение обратно той же карточкой, заполнение дня без дыр и отказ на кривой ввод.
"""

import datetime as dt

import psycopg
from app.person_key import make_key
from app.schedule_grid import hours_from_text

from tests.conftest import _test_dsn, service_today
from tests.integration.test_webapp_profile import _api, init_data

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "792001"}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)", (pid, role))
    conn.commit()
    return pid


def _monday() -> dt.date:
    today = service_today()
    return today - dt.timedelta(days=today.weekday())


def test_fr_c9_неделя_записывается_и_читается_той_же_карточкой(client):
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 792001, "Карточка Неделя")

    days = [
        {"day": (monday + dt.timedelta(days=0)).isoformat(),
         "status": "busy", "to_time": "18:30"},
        {"day": (monday + dt.timedelta(days=1)).isoformat(),
         "status": "free", "from_time": "19:00"},
        {"day": (monday + dt.timedelta(days=2)).isoformat(),
         "status": "busy", "from_time": "16:00", "to_time": "20:00"},
        {"day": (monday + dt.timedelta(days=3)).isoformat(),
         "status": "free"},
        {"day": (monday + dt.timedelta(days=4)).isoformat(),
         "status": "busy", "note": "не будет в Самаре"},
    ]
    r = client.post("/tools/availability/week", headers=HEADERS,
                    json={"start": monday.isoformat(), "days": days})
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 5
    assert r.json()["hours"] == 5 * 16          # каждый день материализован целиком

    body = client.get(f"/tools/availability/week?start={monday.isoformat()}",
                      headers=HEADERS).json()
    assert body["ok"] is True and body["participant_id"] == pid
    assert [d["day"] for d in body["days"]] == [
        (monday + dt.timedelta(days=i)).isoformat() for i in range(7)]

    assert body["days"][0]["status"] == "busy"
    assert (body["days"][0]["from"], body["days"][0]["to"]) == (None, "18:30")
    assert body["days"][1]["status"] == "free" and body["days"][1]["from"] == "19:00"
    assert (body["days"][2]["from"], body["days"][2]["to"]) == ("16:00", "20:00")
    assert body["days"][3]["status"] == "free" and body["days"][3]["text"] == "свободен"
    assert body["days"][4]["note"] == "не будет в Самаре"
    # неотмеченный день — не «свободен», а явное «нет данных»
    assert body["days"][5]["marked"] is False and body["days"][5]["status"] is None
    assert body["days"][6]["marked"] is False


def test_fr_c9_день_заполняется_без_дыр(client):
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 792002, "Карточка Дыры")
    client.post("/tools/availability/week", headers={**HEADERS, "X-Actor-Chat-Id": "792002"},
                json={"start": monday.isoformat(),
                      "days": [{"day": monday.isoformat(), "status": "busy",
                                "to_time": "19:00"}]})
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select hour, kind, is_free, source from availability_hour "
            "where participant_id = %s and day = %s order by hour", (pid, monday)).fetchall()
    assert [row[0] for row in rows] == list(range(8, 24))
    assert all(row[3] == "bot" for row in rows)
    assert [row[1] for row in rows] == ["busy"] * 11 + ["free"] * 5
    assert [row[2] for row in rows] == [False] * 11 + [True] * 5


def test_fr_c9_кривой_ввод_отвергается(client):
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792003, "Карточка Отказ")

    reversed_day = client.post("/tools/availability/week", headers={
        **HEADERS, "X-Actor-Chat-Id": "792003"},
        json={"start": monday.isoformat(),
              "days": [{"day": monday.isoformat(), "status": "busy",
                        "from_time": "20:00", "to_time": "18:00"}]})
    assert reversed_day.status_code == 400
    assert "bad_interval" in reversed_day.json()["detail"]

    outside = client.post("/tools/availability/week", headers={
        **HEADERS, "X-Actor-Chat-Id": "792003"},
        json={"start": monday.isoformat(),
              "days": [{"day": (monday + dt.timedelta(days=8)).isoformat(),
                        "status": "free"}]})
    assert outside.status_code == 400
    assert outside.json()["detail"] == "day_outside_week"


def test_fr_c9_чужую_неделю_видит_только_с_правом_чтения(client):
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792003, "Карточка Своя")
        other = _person(conn, 792004, "Карточка Чужая")

    r = client.get(f"/tools/availability/week?participant_id={other}&start={monday.isoformat()}",
                   headers={**HEADERS, "X-Actor-Chat-Id": "792003"})
    assert r.status_code == 403
    assert "schedule.read.all" in r.json()["detail"]


def test_fr_c9_импорт_из_листа_сохраняет_минуты(client):
    """FR-C9: «до 18:30» из листа остаётся 18:30, хотя часовая сетка округляет до 19:00."""
    monday = _monday()
    text = "занята до 18:30"
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 792005, "Карточка Импорт")
        for hour, kind in hours_from_text(text).items():
            conn.execute(
                "insert into availability_hour"
                " (participant_id, day, hour, is_free, kind, source, raw_text)"
                " values (%s,%s,%s,%s,%s,'sheet_import',%s)",
                (pid, monday, hour, kind == "free", kind, text))
        conn.commit()

    day = client.get(f"/tools/availability/week?start={monday.isoformat()}",
                     headers={**HEADERS, "X-Actor-Chat-Id": "792005"}).json()["days"][0]
    assert day["to"] == "18:30"          # объявленная граница, а не округлённая
    assert day["text"] == "занят до 18:30"


def test_fr_c9_род_берётся_из_профиля(client):
    """FR-C9: в лист и в ответ пишется «занята», если человек так о себе пишет."""
    monday = _monday()
    headers = {**HEADERS, "X-Actor-Chat-Id": "792006"}
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792006, "Карточка Женский")

    saved = client.post("/tools/profile/me", headers=headers, json={"write_gender": "f"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["write_gender"] == "f"

    client.post("/tools/availability/week", headers=headers,
                json={"start": monday.isoformat(),
                      "days": [{"day": monday.isoformat(), "status": "busy",
                                "to_time": "18:30", "note": "не будет в Самаре"}]})
    day = client.get(f"/tools/availability/week?start={monday.isoformat()}",
                     headers=headers).json()["days"][0]
    assert day["text"] == "занята до 18:30 (не будет в Самаре)"
    assert day["note"] == "не будет в Самаре"      # регистр комментария сохранён


def test_fr_c10_шаблон_запоминается_и_заполняет_пустые_дни(client):
    """FR-C10: «обычная неделя» подставляется в пустые дни и не трогает отмеченные."""
    monday = _monday()
    headers = {**HEADERS, "X-Actor-Chat-Id": "792007"}
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792007, "Карточка Шаблон")

    client.post("/tools/availability/week", headers=headers,
                json={"start": monday.isoformat(), "days": [
                    {"day": monday.isoformat(), "status": "busy", "to_time": "18:30"},
                    {"day": (monday + dt.timedelta(days=2)).isoformat(),
                     "status": "free", "from_time": "19:00"}]})

    saved = client.post("/tools/availability/template/from-week", headers=headers,
                        json={"start": monday.isoformat()}).json()
    assert saved["ok"] is True and saved["filled"] == 2

    next_week = monday + dt.timedelta(days=7)
    # в следующей неделе среда уже отмечена — её шаблон не должен затереть
    client.post("/tools/availability/week", headers=headers,
                json={"start": next_week.isoformat(), "days": [
                    {"day": (next_week + dt.timedelta(days=2)).isoformat(),
                     "status": "busy", "to_time": "15:00"}]})

    applied = client.post("/tools/availability/template/apply", headers=headers,
                          json={"start": next_week.isoformat(), "apply": True}).json()
    assert applied["applied"] is True
    assert [row["day"] for row in applied["days"]] == [next_week.isoformat()]
    assert applied["skipped"] == [(next_week + dt.timedelta(days=2)).isoformat()]

    week = client.get(f"/tools/availability/week?start={next_week.isoformat()}",
                      headers=headers).json()["days"]
    assert week[0]["to"] == "18:30"        # понедельник из шаблона
    assert week[2]["to"] == "15:00"        # среда осталась своя


def test_fr_c10_без_шаблона_заполнение_отказывает(client):
    """FR-C10: пустой шаблон — понятная причина, а не пустая неделя молча."""
    monday = _monday()
    headers = {**HEADERS, "X-Actor-Chat-Id": "792008"}
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792008, "Карточка Без Шаблона")

    body = client.post("/tools/availability/template/apply", headers=headers,
                       json={"start": monday.isoformat()}).json()
    assert body["ok"] is False and body["error"] == "no_template"


def _rows(pid: int, day: dt.date) -> list[tuple]:
    with psycopg.connect(_test_dsn()) as conn:
        return conn.execute(
            "select hour, is_free, kind, source, raw_text from availability_hour"
            " where participant_id = %s and day = %s order by hour", (pid, day)).fetchall()


def _webapp_headers(client, chat_id: int) -> dict:
    """Подписанный вход Mini App для человека с этим chat_id."""
    _api()
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_fr_c9_отметка_по_часам_и_карточка_дня_пишут_одинаково(client):
    """З1: два входа в занятость дают одну и ту же строку — путь записи один.

    Раньше `commit` писал `is_free = kind in ('free','preferred')`, а `_save_week`
    — `kind == 'free'`, и «не уверен» из формы читался обратно как занятость.
    """
    monday = _monday()
    headers = {**HEADERS, "X-Actor-Chat-Id": "792009"}
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 792009, "Карточка Один Путь")

    # вход 1 — интервалом часов (канон чата), весь рабочий день
    client.post("/tools/availability/commit", headers=headers,
                json={"chat_id": 792009, "day": monday.isoformat(), "start": 8,
                      "end": 24, "kind": "uncertain", "raw_text": "пока не понятно"})
    by_hours = _rows(pid, monday)

    # тот же день — карточкой дня через Mini App (вход экрана)
    client.post("/webapp/availability/week",
                headers=_webapp_headers(client, 792009),
                json={"start": monday.isoformat(),
                      "days": [{"day": monday.isoformat(), "status": "uncertain"}]})
    by_card = _rows(pid, monday)

    assert by_hours == by_card, "два пути записи дали разные строки"
    assert {(row[2], row[1]) for row in by_card} == {("uncertain", False)}, by_card
    assert all(row[3] == "bot" for row in by_card), "ручная запись должна остаться bot"


def test_fr_c10_шаблон_сохраняется_и_применяется_разными_действиями(client):
    """З8/З5: шаблон сохраняется кнопкой Mini App, читается и применяется обоими входами."""
    monday = _monday()
    headers = {**HEADERS, "X-Actor-Chat-Id": "792010"}
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 792010, "Карточка Шаблон Два Входа")

    client.post("/tools/availability/week", headers=headers,
                json={"start": monday.isoformat(), "days": [
                    {"day": monday.isoformat(), "status": "busy", "to_time": "18:30"}]})

    # сохранение шаблона — входом Mini App (кнопка «Сохранить шаблон недели», З8)
    webapp_headers = _webapp_headers(client, 792010)
    saved = client.post("/webapp/availability/template/from-week",
                        headers=webapp_headers,
                        json={"start": monday.isoformat()}).json()
    assert saved["ok"] is True and saved["filled"] == 1, saved

    # читается одинаково обоими входами — реализация одна
    tools_view = client.get("/tools/availability/template", headers=headers).json()
    assert len(tools_view["days"]) == 1

    next_week = monday + dt.timedelta(days=7)
    preview = client.post("/webapp/availability/template/apply", headers=webapp_headers,
                          json={"start": next_week.isoformat(), "apply": False}).json()
    assert preview["ok"] is True and preview["applied"] is False
    assert preview["days"][0]["text"] == "занят до 18:30", preview

    applied = client.post("/tools/availability/template/apply", headers=headers,
                          json={"start": next_week.isoformat(), "apply": True}).json()
    assert applied["applied"] is True
    week = client.get(f"/tools/availability/week?start={next_week.isoformat()}",
                      headers=headers).json()["days"]
    assert week[0]["to"] == "18:30", "применение шаблона не записалось"

"""Поиск окон через API (UC-13, docs/architecture/40)."""

from datetime import date, timedelta

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    return pid


def _mark(conn, pid: int, day: str, hours: list[int], is_free: bool = True) -> None:
    for hour in hours:
        conn.execute("""insert into availability_hour
                          (participant_id, day, hour, is_free, kind, source)
                        values (%s,%s,%s,%s,'declared','bot')
                        on conflict (participant_id, day, hour) do update
                          set is_free = excluded.is_free""",
                     (pid, day, hour, is_free))


def test_окно_находится_по_данным_двух_людей(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880001, "Первый Тест")
        b = _person(conn, 880002, "Второй Тест")
        for hour in range(18, 22):
            _mark(conn, a, "2026-11-24", [hour])
            _mark(conn, b, "2026-11-24", [hour])
        # занятость до начала окна — чтобы 18:00 было единственным вариантом
        _mark(conn, a, "2026-11-24", list(range(8, 18)), is_free=False)
        _mark(conn, b, "2026-11-24", list(range(8, 18)), is_free=False)
        conn.commit()

    r = client.post("/tools/schedule/slots", headers=auth, json={
        "participants": [a, b], "date_from": "2026-11-24", "date_to": "2026-11-24",
        "min_hours": 2})
    body = r.json()
    assert body["ok"] is True
    assert len(body["windows"]) == 1
    window = body["windows"][0]
    assert (window["from_hour"], window["to_hour"]) == (18, 22)
    assert "18:00–22:00" in body["text"][0]


def test_занятость_одного_убирает_час(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880003, "Третий Тест")
        b = _person(conn, 880004, "Четвёртый Тест")
        for hour in range(18, 22):
            _mark(conn, a, "2026-11-25", [hour])
            _mark(conn, b, "2026-11-25", [hour])
        _mark(conn, b, "2026-11-25", [19], is_free=False)
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "participants": [a, b], "date_from": "2026-11-25", "date_to": "2026-11-25",
        "min_hours": 2}).json()
    spans = [(w["from_hour"], w["to_hour"]) for w in body["windows"]]
    assert (18, 19) not in spans
    assert all(not (w["from_hour"] <= 19 < w["to_hour"]) for w in body["windows"])


def test_человек_без_данных_в_списке_неизвестных(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880005, "Пятый Тест")
        b = _person(conn, 880006, "Шестой Тест")
        for hour in range(18, 21):
            _mark(conn, a, "2026-11-26", [hour])
        _mark(conn, b, "2026-11-26", [9], is_free=False)     # отметился только утром
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "participants": [a, b], "date_from": "2026-11-26", "date_to": "2026-11-26",
        "min_hours": 2}).json()
    assert body["windows"], "отсутствие вечерних отметок не должно убивать окно"
    names = [item["name"] for item in body["unknown"]]
    assert "Шестой Тест" in names


def test_нет_данных_нет_окон(client, auth):
    body = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2027-06-01", "date_to": "2027-06-02", "min_hours": 2}).json()
    assert body["windows"] == []


def test_неуверенность_не_считается_свободой(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880007, "Седьмой Тест")
        for hour in range(18, 22):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s,'2026-11-27',%s,false,'uncertain','bot')""", (a, hour))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "participants": [a], "date_from": "2026-11-27", "date_to": "2026-11-27",
        "min_hours": 1}).json()
    assert body["windows"] == []


def test_диапазон_проверяется(client, auth):
    bad = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2026-11-25", "date_to": "2026-11-20"})
    assert bad.status_code == 400 and "date_to_earlier" in bad.text

    long_range = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert long_range.status_code == 400 and "range_too_long" in long_range.text


def test_нужно_право_schedule_slots(client, auth):
    r = client.post("/tools/schedule/slots", headers={**auth, "X-Actor-Chat-Id": "999999"},
                    json={"min_hours": 2})
    assert r.status_code in (401, 403)


def test_все_действующие_если_список_не_задан(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880008, "Восьмой Тест")
        for hour in range(19, 22):
            _mark(conn, a, "2026-11-28", [hour])
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2026-11-28", "date_to": "2026-11-28", "min_hours": 2}).json()
    assert body["meta"]["participants"] >= 1
    assert body["windows"]


def test_люди_в_pending_участвуют_в_расчёте(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880009, "Девятый Тест")
        for hour in range(19, 22):
            _mark(conn, a, "2026-11-29", [hour])
        conn.execute("update participant set status = 'pending' where id = %s", (a,))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2026-11-29", "date_to": "2026-11-29", "min_hours": 2}).json()
    assert body["windows"], "человек в статусе pending должен учитываться"
    assert body["meta"]["participants"] >= 1


def test_архивные_не_участвуют(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 880010, "Десятый Тест")
        for hour in range(9, 12):
            _mark(conn, a, "2026-11-30", [hour], is_free=False)
        conn.execute("update participant set status = 'archived' where id = %s", (a,))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "date_from": "2026-11-30", "date_to": "2026-11-30", "min_hours": 2}).json()
    ids = body["people"].keys()
    assert str(a) not in ids and a not in body["people"]


def test_режим_максимум_свободных(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        people = [_person(conn, 890001 + i, f"Группа Тест{i}") for i in range(3)]
        for pid in people:
            _mark(conn, pid, "2026-12-01", list(range(18, 22)))
        _mark(conn, people[0], "2026-12-01", [19], is_free=False)
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "mode": "max_free", "date_from": "2026-12-01", "date_to": "2026-12-01",
        "limit": 3}).json()
    assert body["ok"] is True and body["mode"] == "max_free"
    assert body["hours"], "должны найтись часы со свободными"
    best = body["hours"][0]
    assert best["free"] >= 2
    assert "свободны" in body["text"][0]


def test_субъект_по_тексту_с_ролью(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        project = conn.execute(
            "insert into project (name, kind, status) values ('Тестовый спектакль','show','active') "
            "returning id").fetchone()[0]
        actor = _person(conn, 890010, "Актриса Тест")
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, project_id)
                        values (%s, 'Героиня', 'role', %s)""", (actor, project))
        for hour in range(19, 22):
            _mark(conn, actor, "2026-12-02", [hour])
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "text": "найди окна для Героини в Тестовом спектакле",
        "date_from": "2026-12-02", "date_to": "2026-12-02", "min_hours": 2}).json()
    assert body["resolved"]["mode"] == "subset"
    assert body["resolved"]["participant_ids"] == [actor]
    assert body["windows"]


def test_непонятный_субъект_даёт_всех(client, auth):
    body = client.post("/tools/schedule/slots", headers=auth, json={
        "text": "найди окна для Марата", "date_from": "2026-12-03",
        "date_to": "2026-12-03"}).json()
    assert body["resolved"]["mode"] == "all"
    assert "марата" in body["resolved"]["unresolved"]
    assert body["resolved"]["confidence"] < 0.5


def test_политика_показа_требует_всех_с_начала(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 890020, "Показ Первый")
        b = _person(conn, 890021, "Показ Второй")
        # a свободен с 18:00 (с дорогой — с 19:00), b с 19:00 (с дорогой — с 20:00)
        _mark(conn, a, "2026-12-05", list(range(8, 18)), is_free=False)
        for hour in range(18, 23):
            _mark(conn, a, "2026-12-05", [hour])
        _mark(conn, b, "2026-12-05", list(range(8, 19)), is_free=False)
        for hour in range(19, 23):
            _mark(conn, b, "2026-12-05", [hour])
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "show", "participants": [a, b], "date_from": "2026-12-05",
        "date_to": "2026-12-05", "min_hours": 2}).json()
    assert body["mode"] == "policy" and body["policy"] == "show"
    assert body["windows"], "показ должен найтись"
    assert body["windows"][0]["from_hour"] == 20, "оба на месте только с 20:00 (с дорогой)"
    assert body["travel_minutes"][str(a)] == 60, "дорога берётся из карточки участника"


def _hall_event(day: str, from_hour: int, to_hour: int, title: str = "Аренда - Тест") -> dict:
    """Событие календаря, которое занимает зал (docs/architecture/46 §2)."""
    start = f"{day}T{from_hour:02d}:00:00+04:00"
    if to_hour >= 24:                     # конец суток — начало следующего дня
        end_day = date.fromisoformat(day) + timedelta(days=1)
        end = f"{end_day.isoformat()}T00:55:00+04:00"
    else:
        end = f"{day}T{to_hour:02d}:00:00+04:00"
    return {
        "id": f"hall-{day}-{from_hour}",
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "extendedProperties": {"private": {"svoi_kind": "rental"}},
    }


def test_политика_аренды_игнорирует_людей(client, auth, monkeypatch):
    """Аренда без персонала: зал решает, занятость труппы — нет (docs/architecture/40 §4)."""
    from app import core as api

    # зал свободен: единственное событие периода — на другой день
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs:
                        [_hall_event("2026-12-01", 10, 12)])

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 890022, "Аренда Тест")
        _mark(conn, a, "2026-12-06", list(range(8, 23)), is_free=False)
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rental", "date_from": "2026-12-06",
        "date_to": "2026-12-06", "min_hours": 2}).json()
    assert body["mode"] == "venue" and body["policy"] == "rental"
    assert body["windows"], "зал свободен — аренда возможна, хотя труппа занята"
    assert body["venue"]["applied"] is True
    assert (body["windows"][0]["from_hour"], body["windows"][0]["to_hour"]) == (8, 24), \
        "свободный зал отдаётся целиком в границах дня"


def test_окно_для_субаренды_учитывает_занятость_зала(client, auth, monkeypatch):
    """Окно арендатора режется событиями зала, а не отметками людей (С6, docs/architecture/40 §4)."""
    from app import core as api

    # днём в зале репетиция 14:00–16:00, вечером арендатор до 23:55
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [
        _hall_event("2026-12-08", 14, 16, "Репетиция - Тест"),
        _hall_event("2026-12-08", 23, 24, "Аренда - Ночная"),
    ])

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 890023, "Аренда Второй")
        _mark(conn, a, "2026-12-08", list(range(8, 23)), is_free=False)  # людей не спрашиваем
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rental", "date_from": "2026-12-08",
        "date_to": "2026-12-08", "min_hours": 2}).json()
    spans = [(w["from_hour"], w["to_hour"]) for w in body["windows"]]
    assert body["venue"]["applied"] is True
    assert spans, f"свободного времени в зале полно: {body}"
    for start, end in spans:
        assert end <= 14 or start >= 16, f"окно {start}–{end} пересекает репетицию в зале"
        assert end <= 23, f"окно {start}–{end} заходит в занятый ночной час"
    assert (8, 14) in spans, "утро свободно целиком"


def test_аренда_без_занятости_зала_не_выдумывает_окна(client, auth, monkeypatch):
    """Нет данных о зале — нет окна: пустой ответ с причиной вместо «08:00–10:00»."""
    from app import core as api

    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs: [])

    with psycopg.connect(_test_dsn()) as conn:
        a = _person(conn, 890024, "Аренда Третий")
        _mark(conn, a, "2026-12-09", list(range(8, 23)), is_free=True)
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rental", "date_from": "2026-12-09",
        "date_to": "2026-12-09", "min_hours": 2}).json()
    assert body["ok"] is True
    assert body["windows"] == [], "окно аренды нельзя показать без данных о зале"
    assert body["reason"] == "venue_unknown"
    assert body["venue"]["applied"] is False


def test_политика_тренинга_допускает_отсутствие(client, auth):
    with psycopg.connect(_test_dsn()) as conn:
        free_people = [_person(conn, 890030 + i, f"Тренинг Свобод{i}") for i in range(3)]
        busy_person = _person(conn, 890034, "Тренинг Занят")
        for pid in free_people:
            _mark(conn, pid, "2026-12-07", list(range(18, 22)))
        _mark(conn, busy_person, "2026-12-07", list(range(8, 23)), is_free=False)
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "training", "participants": free_people + [busy_person],
        "date_from": "2026-12-07", "date_to": "2026-12-07", "min_hours": 2}).json()
    assert body["windows"], "тренинг можно провести без одного из четырёх"
    assert busy_person in body["windows"][0]["absent"]


def test_дорога_персональная(client, auth):
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        fast = _person(conn, 890040, "Быстрый Тест")
        slow = _person(conn, 890041, "Медленный Тест")
        conn.execute("update participant set travel_minutes = 15 where id = %s", (fast,))
        conn.execute("update participant set travel_minutes = 60 where id = %s", (slow,))
        for pid in (fast, slow):
            _mark(conn, pid, "2026-12-10", list(range(18, 23)))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rehearsal", "participants": [fast, slow], "date_from": "2026-12-10",
        "date_to": "2026-12-10", "min_hours": 2}).json()
    assert body["windows"], "окно должно найтись"
    # быстрый на месте с 18:00, медленному нужно доехать — начинаем в 19:00
    assert body["windows"][0]["from_hour"] == 19
    assert body["travel_minutes"][str(slow)] == 60


def test_аренда_с_персоналом_учитывает_людей(client, auth, monkeypatch):
    import psycopg
    from app import core as api

    from tests.conftest import _test_dsn

    # зал свободен (событие периода — на другой день), проверяем именно персонал
    monkeypatch.setattr(api, "fetch_events", lambda *args, **kwargs:
                        [_hall_event("2026-12-01", 10, 12)])

    with psycopg.connect(_test_dsn()) as conn:
        staff = _person(conn, 890042, "Админ Аренды")
        _mark(conn, staff, "2026-12-11", list(range(8, 23)), is_free=False)
        conn.commit()

    with_staff = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rental", "participants": [staff], "date_from": "2026-12-11",
        "date_to": "2026-12-11", "min_hours": 2}).json()
    assert with_staff["policy"] == "rental_staff"
    assert with_staff["windows"] == [], "администратор занят — окна нет"

    without_staff = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rental", "date_from": "2026-12-11",
        "date_to": "2026-12-11", "min_hours": 2}).json()
    assert without_staff["windows"], "самостоятельная аренда от людей не зависит"


def test_дорога_два_часа_сдвигает_окно(client, auth):
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        local = _person(conn, 890043, "Местный Тест")
        far = _person(conn, 890044, "Дальний Тест")
        conn.execute("update participant set travel_minutes = 0 where id = %s", (local,))
        conn.execute("update participant set travel_minutes = 120 where id = %s", (far,))
        for pid in (local, far):
            _mark(conn, pid, "2026-12-12", list(range(18, 23)))
        conn.commit()

    body = client.post("/tools/schedule/slots", headers=auth, json={
        "policy": "rehearsal", "participants": [local, far], "date_from": "2026-12-12",
        "date_to": "2026-12-12", "min_hours": 2}).json()
    assert body["travel_minutes"][str(far)] == 120
    assert body["travel_minutes"][str(local)] == 0
    assert body["windows"], "окно должно найтись"
    # местный на месте с 18:00, дальнему ехать два часа — окно с 20:00
    assert body["windows"][0]["from_hour"] == 20, "дорога 120 минут сдвигает начало на 2 часа"

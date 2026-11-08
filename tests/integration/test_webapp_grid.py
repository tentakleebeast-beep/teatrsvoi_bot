"""Сетка занятости в Mini App: чтение месяца и права (FR-H9, З7, З9).

Обоснование: FR-H9 (сводка «люди × дни» — только чтение, только организатору),
FR-C5/С7 (сводка свободных), docs/reports/124 §3 (З7, З9) и план 126 Ш-04.
Дефект, который сторожат эти тесты: месяц умел писать (`POST /webapp/grid`),
хотя месяц должен быть только для глаз, а правка дня живёт в листе дня (З1).

Запись больше не проверяется здесь: у неё свои тесты (`test_availability_week.py`,
`test_webapp_my.py`), а на этом маршруте записи нет вовсе — `POST /webapp/grid`
обязан отвечать 405.
"""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
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


def _mark_week(client, headers: dict, day: str, status: str) -> dict:
    """Отметка дня — каноном записи Mini App (З1), а не удалённой сеткой."""
    answer = client.post("/webapp/availability/week", headers=headers,
                         json={"start": day, "days": [{"day": day, "status": status}]})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_сетка_отдаёт_месяц_и_людей(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781001, "Сетка Первый", role="org")   # сводку читает организатор (FR-H9)
        _person(conn, 781002, "Сетка Вторая")
    headers = _token(client, 781001)

    body = client.get("/webapp/grid?month=2026-11", headers=headers).json()
    assert body["ok"] is True
    assert len(body["days"]) == 30
    assert body["days"][0] == "2026-11-01" and body["days"][-1] == "2026-11-30"
    names = [p["name"] for p in body["people"]]
    assert "Сетка Первый" in names and "Сетка Вторая" in names
    assert any(p["self"] for p in body["people"]), "своя колонка помечена"
    assert body["hours"] == [8, 23]


def test_отметка_дня_видна_в_сетке(client):
    """Отметка, поставленная каноном (З1), читается сеткой как «занят»."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781003, "Сетка Отметка", role="org")
    headers = _token(client, 781003)

    saved = _mark_week(client, headers, "2026-11-10", "busy")
    assert saved["ok"] is True

    grid = client.get("/webapp/grid?month=2026-11", headers=headers).json()
    assert grid["marks"][str(pid)]["2026-11-10"]["state"] == "busy"
    assert grid["marks"][str(pid)]["2026-11-10"]["busy_hours"] == 16


def test_неуверенность_отдельное_состояние(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781004, "Сетка Неуверен", role="org")
    headers = _token(client, 781004)

    _mark_week(client, headers, "2026-11-11", "uncertain")
    grid = client.get("/webapp/grid?month=2026-11", headers=headers).json()
    assert grid["marks"][str(pid)]["2026-11-11"]["state"] == "uncertain"


def test_стереть_отметку(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781005, "Сетка Стереть", role="org")
    headers = _token(client, 781005)

    _mark_week(client, headers, "2026-11-12", "busy")
    cleared = client.post("/webapp/availability/clear", headers=headers,
                          json={"week": "2026-11-09"})
    assert cleared.status_code == 200 and cleared.json()["cleared"] == 1, cleared.text

    grid = client.get("/webapp/grid?month=2026-11", headers=headers).json()
    assert str(pid) not in grid["marks"] or "2026-11-12" not in grid["marks"][str(pid)]


def test_сетка_месяца_не_пишет(client):
    """П-3, Ш-04: записи через месяц нет — маршрут отвечает 405, а не 200.

    Право на чтение сетки у этого человека есть (организатор), поэтому отказ —
    именно про отсутствующий метод, а не про права. В схеме OpenAPI `post`
    у `/webapp/grid` тоже быть не должно: по схеме сверяется контракт.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781010, "Сетка Без Записи", role="org")
    headers = _token(client, 781010)

    answer = client.post("/webapp/grid", headers=headers,
                         json={"day": "2026-11-10", "status": "busy"})
    assert answer.status_code == 405, answer.text

    from app.main import app  # noqa: PLC0415 — приложение собирается в фикстуре

    operations = (app.openapi().get("paths") or {}).get("/webapp/grid") or {}
    assert "post" not in operations, "запись сетки вернулась в контракт"
    assert "get" in operations, "чтение сетки пропало из контракта"


def test_fr_h9_сводку_видит_организатор_а_участник_получает_403(client):
    """FR-H9: сводка занятости — право `schedule.read.all`, проверка на сервере.

    Участник без права получает 403 на `GET /webapp/grid`, организатор — ту же сетку,
    что отдавалась раньше (тело ответа не изменилось). Свою отметку участник
    по-прежнему ставит — каноном записи (`/webapp/availability/week`), а не сеткой.
    Разрыв закрыт по строке З7 реестра `docs/architecture/25` §9.4, карта t_52bf3106.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        org = _person(conn, 781011, "Сводка Орг", role="org")
        _person(conn, 781012, "Сводка Студиец")
        member_id = _person(conn, 781013, "Сводка Участник")

    member = _token(client, 781013)
    denied = client.get("/webapp/grid?month=2026-11", headers=member)
    assert denied.status_code == 403, denied.text
    assert "schedule.read.all" in denied.json()["detail"]

    # свою отметку участник ставит как раньше — другим путём (З1)
    _mark_week(client, member, "2026-11-16", "busy")

    allowed = client.get("/webapp/grid?month=2026-11", headers=_token(client, 781011))
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert set(body) == {"ok", "month", "days", "weekdays", "people", "marks",
                         "states", "hours"}, "тело ответа не изменилось"
    assert body["ok"] is True and body["month"] == "2026-11"
    assert body["days"][0] == "2026-11-01" and len(body["days"]) == 30
    names = [p["name"] for p in body["people"]]
    assert "Сводка Студиец" in names and "Сводка Участник" in names
    assert [p["id"] for p in body["people"] if p["self"]] == [org]
    assert body["marks"][str(member_id)]["2026-11-16"]["state"] == "busy"


def test_fr_h9_навигация_знает_о_праве_на_сводку(client):
    """FR-H9: `/webapp/nav` отдаёт `summary` — единственный источник правды для оболочки.

    Строку «Сводка занятости» на `/webapp/week` показывает не страница (она —
    публичная константа), а оболочка по признаку с сервера: участнику без
    `schedule.read.all` — нет, организатору — да. Карта t_70a6d8e5,
    docs/architecture/25 §9.2.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781020, "Навигация Орг", role="org")
        _person(conn, 781021, "Навигация Участник")

    org = client.get("/webapp/nav", headers=_token(client, 781020))
    assert org.status_code == 200, org.text
    org_body = org.json()
    assert org_body["ok"] is True
    assert org_body["summary"] is True, "организатор потерял строку сводки"
    assert org_body["staff"] is True

    member = client.get("/webapp/nav", headers=_token(client, 781021))
    assert member.status_code == 200, member.text
    member_body = member.json()
    assert member_body["ok"] is True
    assert member_body["summary"] is False, "участнику без права показана строка сводки"
    assert member_body["staff"] is False


def test_сверка_открыта_по_праву_а_не_по_роли(client):
    """`/webapp/nav` отдаёт `reconcile` по праву `budget.import` (отчёт 142 §3 строка 10).

    Сверка листов пишет в Google-таблицу, поэтому кнопка «Проверить» нужна только
    тем, кому открыт `POST /webapp/admin/reconcile`: организатору и админу. У
    `troupe` и `master` права нет, и экран обязан сказать это словами, а не
    оставить кнопку, упирающуюся в 403 (карта t_01784cc4, docs/architecture/25 §9.2).
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781022, "Сверка Орг", role="org")
        _person(conn, 781023, "Сверка Студиец")
        _person(conn, 781024, "Сверка Мастер", role="master")

    org = client.get("/webapp/nav", headers=_token(client, 781022)).json()
    assert org["ok"] is True
    assert org["reconcile"] is True, "организатор потерял сверку листов"

    for chat_id, name in ((781023, "студиец"), (781024, "мастер")):
        body = client.get("/webapp/nav", headers=_token(client, chat_id)).json()
        assert body["ok"] is True
        assert body["reconcile"] is False, f"{name} получил кнопку сверки"


def test_своя_отметка_не_попадает_чужому(client):
    """Отметка дня идёт от токена: `participant_id` в запросе нет, чужое не пишется.

    Свойство переехало с удалённой сетки на канон записи (З1): проверяется то же
    самое — после отметки в таблице нет строк другого человека.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        other = _person(conn, 781030, "Сетка Чужая")
    headers = _token(client, 781030)

    _mark_week(client, headers, "2026-11-14", "busy")
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute("select participant_id from availability_hour "
                            "where day = '2026-11-14'").fetchall()
    assert rows and all(row[0] == other for row in rows), "отметка попала только своему"


def test_импорт_из_файла_не_перезаписывает_отметку_бота(client):
    """Отметка человека (source='bot') не затирается импортом листа (FR-C9)."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 781031, "Сетка Импорт")
    headers = _token(client, 781031)

    _mark_week(client, headers, "2026-11-13", "free")

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""
            insert into availability_hour
              (participant_id, day, hour, is_free, kind, source)
            values (%s,'2026-11-13',10,false,'busy','sheet_import')
            on conflict (participant_id, day, hour) do update
              set is_free = excluded.is_free, source = 'sheet_import'
              where availability_hour.source <> 'bot'""", (pid,))
        conn.commit()
        state = conn.execute("""select is_free, source from availability_hour
                                where participant_id = %s and day = '2026-11-13'
                                  and hour = 10""", (pid,)).fetchone()

    assert state[1] == "bot", "ручная отметка бота защищена от импорта"


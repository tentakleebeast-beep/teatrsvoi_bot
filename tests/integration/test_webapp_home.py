"""Хаб отдаёт плитки и счётчики.

Дефект проды 30.10.2026: `/webapp/home/data` падал с KeyError: 0 — соединение
отдаёт словари, а счётчики читались по индексу. Человек видел «не удалось
загрузить меню» вместо хаба.

Строки «Личного» на «Ещё» — docs/development/92 §9.1: строка файлов едет в ответе
данных (`more.personal`), потому что страница — публичная константа.

Блок «Завтра» и ссылка «Весь календарь» (V-131, docs/reports/74-questions.md):
события следующего дня приходят тем же ответом, что «Сегодня», а адрес ссылки
выбирает сервер по праву `schedule.read.all`. Google не вызывается — календарь
подменён фальшивым адаптером, как в tests/integration/test_events_disambiguation.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import psycopg
from app.pages.layout import human_date
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ADMIN_CHAT = 123456789


def _admin(conn):
    """Админ уже есть в тестовой базе — досоздаём только недостающее."""
    row = conn.execute("select id from participant where tg_chat_id = %s",
                       (ADMIN_CHAT,)).fetchone()
    if row is None:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,'Тест Админ',%s,'admin','active') returning id",
            (ADMIN_CHAT, make_key("Тест Админ"))).fetchone()[0]
    else:
        pid = row["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'admin') "
                 "on conflict do nothing", (pid,))
    conn.execute("insert into participant (tg_chat_id, display_name, person_key, role, status) "
                 "values (777,'Ждущий',%s,'troupe','pending')", (make_key("Ждущий"),))
    conn.execute("insert into feedback (kind, text, status) values ('bug','проверка','new')")
    conn.commit()
    return pid


def test_хаб_отдаёт_блоки_и_счётчики(client):
    """Контракт хаба после переделки (план 94, Ш-2/Ш-7): блоки, а не разделы с плитками.

    Проверяем то, ради чего тест и был: админ видит «требует внимания» со счётчиками
    и «Управление» со своими разделами, а недоступное не приходит вовсе.
    """
    _api()  # выставляет BOT_TOKEN для проверки initData
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        _admin(conn)
    auth = client.post("/webapp/auth", json={"init_data": init_data(ADMIN_CHAT)})
    assert auth.status_code == 200, auth.text
    token = auth.json()["token"]

    response = client.get("/webapp/home/data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True

    attention = body["attention"]
    assert attention, "хаб пуст: ни одного блока «требует внимания»"
    urls = {row["url"] for row in attention}
    assert "/webapp/admin/t/feedback" in urls, "не пришёл блок обращений"
    assert body["counts"]["feedback"] >= 1

    # Заявок в боте больше нет (docs/development/182 §4.1, V-116/V-117): раздел
    # `pending` снят из реестра, и счётчик не должен вернуться ни строкой, ни числом.
    assert "pending" not in body["counts"], "вернулся счётчик заявок"
    assert all("/pending" not in row["url"] and row["title"] != "Заявки" for row in attention), \
        "вернулся бейдж «Заявки»"

    # Бейдж «Крупные суммы, ждут подтверждения» убран решением 30.10.2026: механизма
    # второго подтверждения нет, счётчик в approval_request не мог быть непустым.
    assert "approvals" not in body["counts"], "вернулся счётчик крупных сумм"
    assert all(row["title"] != "Крупные суммы" for row in attention), \
        "вернулся бейдж «Крупные суммы, ждут подтверждения»"

    feedback = next(row for row in attention if row["url"] == "/webapp/admin/t/feedback")
    assert feedback["meta"] == "открытые", "нет счётчика обращений в блоке внимания"
    assert feedback["count"] >= 1

    management = {row["url"] for row in body["more"]["management"]}
    assert "/webapp/admin/t/people" in management, "в «Управлении» нет людей"
    assert body["staff"] is True

    # «Личное»: строки едут данными, потому что страница — публичная константа.
    # Заметки — личный раздел у всех (FR-F7), файлы — по `files.access` (V-039 (в)).
    personal = body["more"]["personal"]
    assert [row["url"] for row in personal] == ["/webapp/files", "/webapp/notes"], personal
    assert personal[0]["title"] == "Файлы студии"
    assert personal[0]["meta"] == "записи, фонограммы, тексты"
    assert personal[1]["title"] == "Заметки"


def test_у_студийца_личное_без_файлов(client):
    """Файлы открыты по `files.access` (V-039 (в)), заметки — по `notes.write` (FR-F7).

    У студийца `files.access` снят миграцией 062: решение владельца — раздел видят
    admin, org и master, и «нет права» студиец читает словами, а не пустым списком.
    """
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,'Тест Студиец',%s,'troupe','active') returning id",
            (770500, make_key("Тест Студиец"))).fetchone()["id"]
        # Возможности берутся из participant_role, а не из participant.role: без строки
        # роли у человека «Возможностей: 0» и заметок он не увидит (та же причина, что
        # в обращении №24).
        conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                     (pid,))
        conn.commit()

    auth = client.post("/webapp/auth", json={"init_data": init_data(770500)})
    assert auth.status_code == 200, auth.text
    token = auth.json()["token"]

    response = client.get("/webapp/home/data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    personal = response.json()["more"]["personal"]
    assert [row["url"] for row in personal] == ["/webapp/notes"], personal
    assert all(row["url"] != "/webapp/files" for row in personal), "студийцу файлы не открыты"


# ------------------------------------------------------------------ V-131: «Завтра»


class _FakeList:
    """`list(...).execute()` адаптера календаря: отдаёт заранее заданные события."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def execute(self) -> dict:
        return {"items": self._items}


class _FakeEvents:
    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.params: dict = {}

    def list(self, **kwargs) -> _FakeList:
        self.params = kwargs
        return _FakeList(self._items)


class _FakeCalendar:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def events(self) -> _FakeEvents:
        return _FakeEvents(self._items)


def _calendar(monkeypatch, items: list[dict]) -> None:
    """Календарь студии отвечает заданными событиями, Google не вызывается."""
    from app import core as api

    monkeypatch.setattr(api, "CALENDAR_ID_READ", "read-calendar")
    monkeypatch.setattr(api, "_calendar", lambda: _FakeCalendar(items))


def _tomorrow() -> date:
    from app import core as api

    return datetime.now(api.TZ).date() + timedelta(days=1)


def _event(day: date, hour: int, title: str, place: str = "Зал") -> dict:
    """Событие календаря в том же виде, что отдаёт Google API."""
    return {"start": {"dateTime": f"{day.isoformat()}T{hour:02d}:00:00+04:00"},
            "summary": title, "location": place}


def _person(conn, chat_id: int, name: str, role: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    _api()
    auth = client.post("/webapp/auth", json={"init_data": init_data(chat_id)})
    assert auth.status_code == 200, auth.text
    return {"Authorization": f"Bearer {auth.json()['token']}"}


def test_завтра_список_событий_и_ссылка_на_сводку(client, monkeypatch):
    """V-131: админ видит завтрашние события и вход в сводку (право есть).

    Список — не одно событие: владелец просил «список событий на следующий день»,
    поэтому `events` массив, а поля события те же, что у «Сегодня».
    """
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        _admin(conn)
    day = _tomorrow()
    _calendar(monkeypatch, [_event(day, 12, "Прогон", "Зал"),
                            _event(day, 19, "Репетиция", "Сцена")])

    body = client.get("/webapp/home/data", headers=_token(client, ADMIN_CHAT)).json()

    assert body["ok"] is True
    tomorrow = body["tomorrow"]
    assert tomorrow["label"] == "завтра, " + human_date(day)
    assert [row["time"] for row in tomorrow["events"]] == ["12:00", "19:00"]
    assert [row["title"] for row in tomorrow["events"]] == ["Прогон", "Репетиция"]
    assert [row["place"] for row in tomorrow["events"]] == ["Зал", "Сцена"]
    assert all(set(row) == {"time", "title", "place"} for row in tomorrow["events"]), \
        "в событие попал служебный день"
    assert body["calendar_url"] == "/webapp/week/summary", "админ не получил сводку"


def test_пустой_завтра_и_ссылка_без_права_сводки(client, monkeypatch):
    """V-131: пустой день — пустой список, а не отсутствие блока; без права — /week.

    Студиец без `schedule.read.all` получает свой экран недели: ссылка на сводку
    вела бы его на 403 (FR-H9, docs/development/92 §7).
    """
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        _person(conn, 770501, "Тест Завтра", "troupe")
    _calendar(monkeypatch, [])

    body = client.get("/webapp/home/data", headers=_token(client, 770501)).json()

    assert body["ok"] is True
    assert body["tomorrow"]["events"] == [], "пустой день не пришёл пустым списком"
    assert body["tomorrow"]["label"].startswith("завтра, ")
    assert body["calendar_url"] == "/webapp/week", "студийцу открыли сводку"


def test_календарь_не_прочитан_главная_живёт(client):
    """V-131: без календаря `tomorrow` — null, ответ 200 и остальные блоки на месте."""
    _api()
    with psycopg.connect(_test_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        _person(conn, 770502, "Тест Без Календаря", "troupe")

    response = client.get("/webapp/home/data", headers=_token(client, 770502))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tomorrow"] is None, "непрочитанный календарь выдал себя за пустой день"
    assert body["today"] == {}
    assert body["calendar_url"] == "/webapp/week"
    assert body["week"]["days"] == 7


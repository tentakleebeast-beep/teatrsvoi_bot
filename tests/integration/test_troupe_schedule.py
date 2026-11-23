"""V-132: актёр видит расписание студии, но не чужие личные данные.

Решение владельца 23.11.2026 (`docs/reports/194-anketa-aktyory-razbor.md` §6,
`docs/reports/74-questions.md` строка V-132): события, время, место и тип видны всем
действующим ролям, а телефоны, взносы, заметки, дорога и день рождения — нет.
До карты роль `troupe` не имела ни `schedule.read.all`, ни `schedule.write`
(`db/init/008_rbac_capabilities.sql:82-83`), поэтому календарь и карточка события
отвечали 403, и актёр узнавал о репетиции из чата ВК или из таблицы.

Что проверяется:

1. миграция `076` выдаёт `troupe` только чтение (`schedule.read.all`), записи нет;
   повторный накат файла ничего не меняет;
2. `GET /tools/calendar/events` и `GET /tools/events/{id}` отдают актёру данные
   расписания, и в ответе нет личных полей — проверяется набор ключей, а не значения:
   новое личное поле в ответе сломает тест, а не «спрячется» на клиенте;
3. актёр не может создать, перенести или отменить событие: 403 и на чатовых
   маршрутах (`schedule.write`), и на путях Mini App (`/webapp/preview`,
   `/webapp/assign` — они звали обработчики напрямую и прав не проверяли).

Обоснование: FR-A6 (права решает сервер, оболочка только не показывает),
FR-H9 (сводка занятости закрыта `schedule.read.all`), решение V-132,
`docs/architecture/02-access-control.md` §1.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "init" / "076_troupe_schedule_read.sql"

#: Личные поля, которых в ответах расписания быть не должно (решение V-132).
PERSONAL = {"phone", "dues", "dues_total", "travel_minutes", "birth_date",
            "notes", "note", "comment"}

#: Разрешённые поля календаря и карточки события: контракт, а не сегодняшний ответ.
CALENDAR_ITEM = {"id", "title", "start", "end", "location", "status", "kind", "link"}
CARD_EVENT = {"event_id", "title", "kind", "kind_title", "status", "date", "weekday",
              "from_hour", "to_hour", "starts_at", "ends_at", "venue", "venue_name",
              "participants", "in_calendar",
              # V-134: своё время участия и (для `schedule.write`) чужие строки
              "participation", "my_time"}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, %s, 'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s, %s)",
                 (pid, role))
    conn.commit()
    return pid


def _headers(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _keys(value) -> set[str]:
    """Все ключи объекта вместе с вложенными: личное поле не спрячется в участнике."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


class _FakeEvents:
    """Календарь Google: один ответ, без сети (в тестах Google не вызывается)."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def list(self, **_kwargs) -> _FakeEvents:
        return self

    def execute(self) -> dict:
        return {"items": self.items}


class _FakeCalendar:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def events(self) -> _FakeEvents:
        return _FakeEvents(self.items)


def _event_row(conn, creator: int, people: list[int]) -> int:
    start = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    return conn.execute(
        "insert into event_plan (title, kind, starts_at, ends_at, location,"
        " participant_ids, status, created_by)"
        " values ('Репетиция', 'rehearsal', %s, %s, 'hall', %s, 'confirmed', %s)"
        " returning id",
        (start, start + dt.timedelta(hours=2), people, creator)).fetchone()[0]


def test_v132_миграция_выдаёт_труппе_чтение_и_не_выдаёт_запись(test_db: str) -> None:
    """076: у `troupe` есть `schedule.read.all`, прав записи нет, повтор — no-op."""
    sql = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(test_db, autocommit=True) as conn:
        conn.execute(sql)                      # повторный накат применённого файла
        caps = {row[0] for row in conn.execute(
            "select capability from rbac_role_capability where role = 'troupe'")}
        rows = conn.execute(
            "select count(*) from rbac_role_capability"
            " where role = 'troupe' and capability = 'schedule.read.all'").fetchone()[0]
    assert "schedule.read.all" in caps
    assert rows == 1, "повторный накат 076 продублировал выдачу"
    assert not ({"schedule.write", "calendar.write", "schedule.slots"} & caps), \
        f"труппе выдали запись: {sorted(caps)}"


def test_v132_календарь_отдаёт_события_без_личных_полей(client, monkeypatch) -> None:
    """Актёр получает список событий; телефонов, взносов, дороги и дня рождения нет."""
    from app import core as api_core  # noqa: PLC0415

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 778401, "Актёр Календарь")

    monkeypatch.setattr(api_core, "CALENDAR_ID_READ", "test-calendar-id")
    monkeypatch.setattr(api_core, "_calendar", lambda: _FakeCalendar([{
        "id": "ev-1", "summary": "Репетиция", "status": "confirmed",
        "location": "Зал", "htmlLink": "https://calendar.example/ev-1",
        "start": {"dateTime": "2026-11-24T19:00:00+04:00"},
        "end": {"dateTime": "2026-11-24T21:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_kind": "rehearsal"}},
    }]))

    answer = client.get("/tools/calendar/events?days=7", headers=_headers(778401))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ok"] is True and body["count"] == 1, body
    assert set(body["data"][0]) == CALENDAR_ITEM, "контракт события календаря разошёлся"
    assert not (PERSONAL & _keys(body["data"]))
    assert "phone" not in json.dumps(body["data"], ensure_ascii=False)


def test_v132_карточка_события_без_личных_полей(client) -> None:
    """Актёр читает карточку события: только расписание, участник — id и имя."""
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 778402, "Актёр Карточка")
        partner = _person(conn, 778403, "Партнёр Карточка")
        event_id = _event_row(conn, actor, sorted([actor, partner]))
        conn.commit()

    answer = client.get(f"/tools/events/{event_id}", headers=_headers(778402))
    assert answer.status_code == 200, answer.text
    event = answer.json()["event"]
    assert set(event) == CARD_EVENT, "контракт карточки события разошёлся"
    assert {row["id"] for row in event["participants"]} == {actor, partner}
    for row in event["participants"]:
        assert set(row) == {"id", "name"}, "в карточке участника появились лишние поля"
    assert not (PERSONAL & _keys(event)), f"личные поля в карточке: {sorted(_keys(event))}"


def test_v132_актёр_не_может_записать_событие(client) -> None:
    """Создание, перенос и отмена — 403 и в чатовых маршрутах, и в Mini App."""
    with psycopg.connect(_test_dsn()) as conn:
        actor = _person(conn, 778404, "Актёр Без Записи")
        event_id = _event_row(conn, actor, [actor])
        conn.commit()

    headers = _headers(778404)
    tomorrow = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    created = client.post("/tools/events/assign", headers=headers, json={})
    preview = client.post("/tools/events/preview", headers=headers,
                          json={"text": "назначь репетицию завтра"})
    moved = client.post("/tools/events/move", headers=headers,
                        json={"event_id": event_id, "date": tomorrow,
                              "from_hour": 19, "to_hour": 21})
    cancelled = client.post("/tools/events/cancel", headers=headers,
                            json={"event_id": event_id})

    for answer, name in ((created, "assign"), (preview, "preview"),
                         (moved, "move"), (cancelled, "cancel")):
        assert answer.status_code == 403, f"{name}: {answer.status_code} {answer.text}"
        assert "schedule.write" in answer.json()["detail"]

    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from event_plan where id = %s",
                              (event_id,)).fetchone()[0]
    assert status == "confirmed", "отказ по правам всё-таки записал событие"


def test_v132_mini_app_не_создаёт_событие_актёру(client) -> None:
    """`/webapp/preview` и `/webapp/assign` проверяют право сами, а не по экрану."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 778405, "Актёр Миниапп")

    token = client.post("/webapp/auth", json={"init_data": init_data(778405)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    preview = client.post("/webapp/preview", headers=headers, json={"text": "репетиция"})
    assigned = client.post("/webapp/assign", headers=headers, json={})
    assert preview.status_code == 403, preview.text
    assert assigned.status_code == 403, assigned.text


def test_v132_чужая_неделя_без_заметок(client) -> None:
    """Чужая неделя — расписание, но свободный комментарий к дню — личное (V-132).

    Часы, статус и текст карточки видны всем действующим ролям; комментарий
    («не будет в Самаре») — только тому, у кого есть `participants.contact`,
    как и полный телефон.
    """
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 778407, "Актёр Своя Неделя")
        other = _person(conn, 778408, "Актёр Своя Заметка")
        _person(conn, 778409, "Орг Неделя", role="org")

    filled = client.post("/tools/availability/week", headers=_headers(778408), json={
        "start": monday.isoformat(),
        "days": [{"day": (monday + dt.timedelta(days=4)).isoformat(),
                  "status": "busy", "note": "не будет в Самаре"}]})
    assert filled.status_code == 200, filled.text

    actor = client.get(f"/tools/availability/week?participant_id={other}"
                       f"&start={monday.isoformat()}", headers=_headers(778407))
    assert actor.status_code == 200, actor.text
    days = {day["day"]: day for day in actor.json()["days"]}
    friday = days[(monday + dt.timedelta(days=4)).isoformat()]
    assert friday["status"] == "busy", "расписание чужой недели пропало"
    assert friday["note"] == "", "актёру отдали чужую заметку к дню"

    org = client.get(f"/tools/availability/week?participant_id={other}"
                     f"&start={monday.isoformat()}", headers=_headers(778409))
    org_days = {day["day"]: day for day in org.json()["days"]}
    assert org_days[(monday + dt.timedelta(days=4)).isoformat()]["note"] == "не будет в Самаре"


def test_v132_без_права_чтения_расписание_закрыто(client) -> None:
    """Право чтения отключается лично: отказ 403, а не «показали всё равно»."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        pid = _person(conn, 778406, "Актёр Без Чтения")
        conn.execute(
            "insert into participant_capability_override"
            " (participant_id, capability, allowed, reason)"
            " values (%s, 'schedule.read.all', false, 'проверка V-132')", (pid,))

    assert client.get("/tools/calendar/events", headers=_headers(778406)).status_code == 403
    with psycopg.connect(_test_dsn()) as conn:
        event_id = _event_row(conn, pid, [pid])
        conn.commit()
    assert client.get(f"/tools/events/{event_id}",
                      headers=_headers(778406)).status_code == 403

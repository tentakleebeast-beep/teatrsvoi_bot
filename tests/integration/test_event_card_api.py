"""HTTP-контракт карточки события Mini App (docs/development/190 §3, AC-5…AC-7).

Обоснование: FR-D3, `docs/development/190` §3.1–§3.4, AC-5, AC-6, AC-7, AC-8.
Карточка открывает событие любого происхождения: людское (без строки в базе) и
событие бота (маркер `svoi_event_id` + `event_plan`). Ключ — календарь и id
события Google, а не `event_plan.id` — иначе 446 людских событий не открывались.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import _test_dsn, google_error
from tests.integration.polls_helpers import person, token
from tests.integration.test_webapp_profile import init_data

ADMIN_CHAT = 123456789

HUMAN_EVENT = {
    "id": "human-1", "summary": "Запись в Google", "status": "confirmed",
    "start": {"dateTime": "2026-11-24T19:00:00+04:00"},
    "end": {"dateTime": "2026-11-24T21:30:00+04:00"},
}

BOT_EVENT = {
    "id": "bot-1", "summary": "[С] Жизнь насекомых", "status": "confirmed",
    "description": "Сбор за час до начала",
    "start": {"dateTime": "2026-11-24T19:00:00+04:00"},
    "end": {"dateTime": "2026-11-24T21:30:00+04:00"},
    "colorId": "11",
    "extendedProperties": {"private": {"svoi_event_id": "412", "svoi_kind": "show"}},
}


class FakeRequest:
    def __init__(self, answer=None, error=None):
        self.answer = answer or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.answer


class FakeEvents:
    def __init__(self, answer=None, error=None, item=None):
        self.answer = answer
        self.error = error
        self.item = item

    def get(self, calendarId=None, eventId=None, **_kwargs):
        if self.item is not None and eventId != self.item.get("id"):
            return FakeRequest(error=google_error(404))
        return FakeRequest(self.item, self.error)

    def list(self, **_params):
        return FakeRequest(self.answer, self.error)


class FakeService:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


def _patch_google(monkeypatch, item: dict | None = None, error=None):
    """Боевой календарь в настройках и подменённый клиент Google."""
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-cal")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-cal")
    events = FakeEvents(item=item, error=error)
    monkeypatch.setattr(core, "_calendar", lambda: FakeService(events))
    monkeypatch.setattr(core, "_calendar_write", lambda: FakeService(events))
    return events


def _bot_event(plan_id: int) -> dict:
    """Событие бота с маркером на реальную строку `event_plan` фикстуры."""
    event = dict(BOT_EVENT)
    event["extendedProperties"] = {"private": {"svoi_event_id": str(plan_id),
                                               "svoi_kind": "show"}}
    return event


def _person(chat_id: int, name: str, role: str = "troupe") -> int:
    """Действующий участник с ролью: права считаются по роли из базы."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return person(conn, chat_id, name, role)


def _participant_id(chat_id: int) -> int:
    """Id участника фикстуры по chat_id."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute("select id from participant where tg_chat_id = %s",
                            (chat_id,)).fetchone()[0]


def _add_plan(created_by: int, gcal_event_id: str = "bot-1") -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, location,
                                       participant_ids, status, gcal_event_id,
                                       gcal_calendar_id, created_by)
               values ('Жизнь насекомых', 'show',
                       '2026-11-24T19:00:00+04', '2026-11-24T21:30:00+04',
                       'stage', '{}', 'confirmed', %s, 'draft', %s) returning id""",
            (gcal_event_id, created_by)).fetchone()
    return row[0]


def test_людское_событие_открывается_карточкой(client, monkeypatch) -> None:
    """AC-5: людское событие — 200, `plan_id: null`, `human_event`, «Без типа»."""
    _patch_google(monkeypatch, item=HUMAN_EVENT)
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/card?calendar=read&event_id=human-1",
                        headers=headers)
    assert answer.status_code == 200
    event = answer.json()["event"]
    assert event["plan_id"] is None
    assert event["editable"] is False and event["not_editable_reason"] == "human_event"
    assert event["kind"] is None and event["kind_title"] == "Без типа"
    assert event["title"] == "Запись в Google" and event["title_raw"] == "Запись в Google"


def test_событие_бота_отдаёт_создателя_и_состояние(client, monkeypatch) -> None:
    """AC-6: событие бота — создатель, описание из Google, `state`, `editable`."""
    _patch_google(monkeypatch, item=BOT_EVENT)
    headers = token(client, ADMIN_CHAT)
    owner = _participant_id(ADMIN_CHAT)
    plan_id = _add_plan(owner)
    _patch_google(monkeypatch, item=_bot_event(plan_id))
    answer = client.get("/webapp/events/card?calendar=draft&event_id=bot-1",
                        headers=headers)
    assert answer.status_code == 200
    event = answer.json()["event"]
    assert event["plan_id"] == plan_id
    assert event["letter"] == "С" and event["kind"] == "show"
    assert event["kind_title"] == "Спектакль" and event["title"] == "Жизнь насекомых"
    assert event["description"] == "Сбор за час до начала"
    assert event["color_id"] == 11 and event["state"] == "draft"
    assert event["creator"]["name"] == "Иванов Виктор"
    assert event["editable"] is True and event["not_editable_reason"] is None


def test_чужое_событие_бота_не_переносится(client, monkeypatch) -> None:
    """AC-6: у чужого события `editable: false` с причиной `not_your_event`."""
    _patch_google(monkeypatch, item=BOT_EVENT)
    stranger = _person(777001, "Пётр Соколов", "troupe")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("insert into participant_capability_override "
                     "(participant_id, capability, allowed, reason) "
                     "values (%s, 'schedule.read.all', true, 'тест карточки')",
                     (stranger,))
    plan_id = _add_plan(stranger)
    _patch_google(monkeypatch, item=_bot_event(plan_id))
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/card?calendar=draft&event_id=bot-1",
                        headers=headers)
    event = answer.json()["event"]
    assert event["editable"] is False and event["not_editable_reason"] == "not_your_event"


def test_боевой_календарь_закрыт_для_переноса(client, monkeypatch) -> None:
    """AC-7: `calendar=read` — всегда `false` с причиной `live_calendar`."""
    _patch_google(monkeypatch, item=BOT_EVENT)
    headers = token(client, ADMIN_CHAT)
    owner = _participant_id(ADMIN_CHAT)
    _add_plan(owner)
    answer = client.get("/webapp/events/card?calendar=read&event_id=bot-1",
                        headers=headers)
    event = answer.json()["event"]
    assert event["editable"] is False and event["not_editable_reason"] == "live_calendar"
    assert event["state"] == "live"


def test_событие_без_строки_в_базе_отдельная_причина(client, monkeypatch) -> None:
    """§3.3: маркер бота есть, строки нет — `plan_missing`, а не «людское»."""
    _patch_google(monkeypatch, item=BOT_EVENT)
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/card?calendar=draft&event_id=bot-1",
                        headers=headers)
    event = answer.json()["event"]
    assert event["plan_id"] is None
    assert event["editable"] is False and event["not_editable_reason"] == "plan_missing"


def test_нет_события_в_календаре(client, monkeypatch) -> None:
    """§3.4: `event_not_found` — 404, а не пустая карточка."""
    _patch_google(monkeypatch, item=HUMAN_EVENT)
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/card?calendar=read&event_id=nope",
                        headers=headers)
    assert answer.status_code == 404
    assert answer.json()["code"] == "event_not_found"


def test_без_права_отказ_словами(client, monkeypatch) -> None:
    """§3.1: право `schedule.read.all`; отказ — 403 со словами, а не молчание.

    С V-132 право есть у роли `troupe` (актёр видит расписание студии), поэтому
    отказ проверяется на праве, снятом лично.
    """
    _patch_google(monkeypatch, item=HUMAN_EVENT)
    _person(777002, "Актёр Тестов", "troupe")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            "insert into participant_capability_override"
            " (participant_id, capability, allowed, reason)"
            " select id, 'schedule.read.all', false, 'проверка V-132'"
            "   from participant where tg_chat_id = 777002")
    headers = token(client, 777002)
    answer = client.get("/webapp/events/card?calendar=read&event_id=human-1",
                        headers=headers)
    assert answer.status_code == 403
    assert "расписание" in answer.json()["detail"]


def test_параметры_карточки_проверяются(client, monkeypatch) -> None:
    """§3.4: `bad_calendar` и `event_id_required` — разные коды, оба 400."""
    _patch_google(monkeypatch, item=HUMAN_EVENT)
    headers = token(client, ADMIN_CHAT)
    bad = client.get("/webapp/events/card?calendar=month&event_id=human-1",
                     headers=headers)
    assert bad.status_code == 400 and bad.json()["code"] == "bad_calendar"
    empty = client.get("/webapp/events/card?calendar=read", headers=headers)
    assert empty.status_code == 400 and empty.json()["code"] == "event_id_required"


def test_календарь_не_настроен_отдельный_код(client, monkeypatch) -> None:
    """§3.4: пустой id календаря — 503 `calendar_id_missing`, а не 502."""
    from app import core

    _patch_google(monkeypatch, item=HUMAN_EVENT)
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "")
    headers = token(client, ADMIN_CHAT)
    answer = client.get("/webapp/events/card?calendar=read&event_id=human-1",
                        headers=headers)
    assert answer.status_code == 503
    assert answer.json()["code"] == "calendar_id_missing"

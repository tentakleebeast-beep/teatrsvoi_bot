"""Создание и удаление события — только в черновом календаре (186 §5.2–§5.3).

Обоснование: docs/development/186 §5.2, §5.3; дизайн 185 D10.
Человек переносит черновик в боевой календарь руками. Бот видит это по маркеру
`svoi_event_id` и удаляет **только** свой черновик; боевое событие не трогается.
Отсутствие события отменой не считается: «нет» — это не «отменил».

Проверка «событие живо в черновом» (`api/events._draft_event_exists`) смотрит ещё и
`status`: `events.delete` не убирает событие из выдачи `events.get`, Google отдаёт его
по id со `status="cancelled"` (живой прогон 23.11.2026, карта Д-2). Поэтому успешный
ответ `get` сам по себе не доказательство существования.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app import calendar_sync, google_targets

START = datetime(2026, 12, 19, 18, 0, tzinfo=UTC)
END = datetime(2026, 12, 19, 21, 0, tzinfo=UTC)


class FakeConn:
    """Соединение без БД: планы событий и список выполненных запросов."""

    def __init__(self, plans: list[dict]) -> None:
        self.plans = plans
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or ()))
        return self

    def fetchall(self):
        return self.plans

    def fetchone(self):
        return None

    def audit_actions(self) -> list[str]:
        return [params[3] for sql, params in self.calls
                if sql.startswith("insert into audit_event") and len(params) > 3]


class FakeRequest:
    def __init__(self, answer: dict | None = None, error: Exception | None = None) -> None:
        self.answer = answer or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.answer


class FakeEvents:
    def __init__(self, items: list[dict], *, event: dict | None = None,
                 get_error: Exception | None = None) -> None:
        self.items = items
        self.event = event if event is not None else {}
        self.get_error = get_error
        self.gets: list[tuple] = []
        self.deleted: list[tuple[str, str]] = []
        self.updated: list[tuple[str, str]] = []
        self.inserts: list[dict] = []

    def get(self, calendarId=None, eventId=None, **_kwargs):
        self.gets.append((calendarId, eventId))
        return FakeRequest(self.event, self.get_error)

    def list(self, **params):
        return FakeRequest({"items": self.items})

    def delete(self, calendarId=None, eventId=None, **_kwargs):
        self.deleted.append((calendarId, eventId))
        return FakeRequest({})

    def update(self, calendarId=None, eventId=None, body=None, **_kwargs):
        self.updated.append((calendarId, eventId))
        return FakeRequest({"id": eventId})

    def insert(self, calendarId=None, body=None, **_kwargs):
        self.inserts.append({"calendar": calendarId, "body": body})
        return FakeRequest({"id": "draft-1"})


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self):
        return self._events


def _event(plan_id: int | None, *, event_id: str = "evt-1", status: str = "confirmed") -> dict:
    private = {"svoi_event_id": str(plan_id)} if plan_id is not None else {"чужое": "да"}
    return {"id": event_id, "summary": "Репетиция", "status": status,
            "start": {"dateTime": START.isoformat()}, "end": {"dateTime": END.isoformat()},
            "extendedProperties": {"private": private}}


def _live(monkeypatch, items: list[dict], draft: FakeEvents) -> tuple[FakeConn, FakeEvents]:
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")
    read = FakeEvents(items)
    conn = FakeConn([{"id": 1, "status": "planned", "gcal_event_id": "draft-1"}])
    calendar_sync.scan(conn, read_service=FakeService(read),
                       draft_service=FakeService(draft),
                       time_from=START, time_to=END)
    return conn, read


def test_перенос_чистит_только_черновик(monkeypatch):
    """Маркер в боевом — удаляется черновик, аудит `event.draft_cleaned`."""
    draft = FakeEvents([])
    conn, live = _live(monkeypatch, [_event(1, event_id="live-9")], draft)
    assert live.deleted == [], "боевое событие удалять нельзя ни при каких условиях"
    assert draft.deleted == [("draft-calendar", "draft-1")]
    assert "event.draft_cleaned" in conn.audit_actions()
    assert any(sql.startswith("update event_plan set gcal_event_id = null")
               for sql, _ in conn.calls)


def test_отмена_в_боевом_переносит_статус_в_базу(monkeypatch):
    """`status=cancelled` у события с нашим маркером — в базе `cancelled`."""
    draft = FakeEvents([])
    conn, live = _live(monkeypatch, [_event(1, status="cancelled")], draft)
    assert live.deleted == [] and draft.deleted == []
    assert any(sql.startswith("update event_plan set status = 'cancelled'")
               for sql, _ in conn.calls)
    assert "event.cancelled_in_google" in conn.audit_actions()


def test_чужое_событие_в_боевом_не_трогаем(monkeypatch):
    """Событие без маркера — не наше: ни удаления, ни правки базы."""
    draft = FakeEvents([])
    conn, _live_events = _live(monkeypatch, [_event(None)], draft)
    assert draft.deleted == []
    assert not [sql for sql, _ in conn.calls if sql.startswith("update event_plan")]


def test_пустой_календарь_не_отмена(monkeypatch):
    """Нет события в боевом — не повод менять статус: «нет» ≠ «отменил»."""
    draft = FakeEvents([])
    conn, _live_events = _live(monkeypatch, [], draft)
    assert not [sql for sql, _ in conn.calls if "status = 'cancelled'" in sql]


def test_dry_run_ничего_не_меняет(monkeypatch):
    """«Сверить» с `dry_run` только отчитывается: ни удаления, ни записи в базу."""
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")
    draft = FakeEvents([])
    conn = FakeConn([{"id": 1, "status": "planned", "gcal_event_id": "draft-1"}])
    report = calendar_sync.scan(conn, read_service=FakeService(FakeEvents([_event(1)])),
                                draft_service=FakeService(draft),
                                time_from=START, time_to=END, dry_run=True)
    assert report["moved_to_live"], "найденное должно попасть в отчёт"
    assert draft.deleted == []
    assert not [sql for sql, _ in conn.calls
                if sql.startswith(("update event_plan", "insert into audit_event"))]


def test_маркер_события_это_ид_плана():
    """`svoi_event_id` — `event_plan.id`: сверка идёт по нему, а не по названию."""
    assert calendar_sync._event_id(_event(17)) == 17
    assert calendar_sync._event_id(_event(None)) is None


def test_реестр_не_даёт_писать_в_боевой(monkeypatch):
    """Ключ записи есть только у черновика: у боевого календаря его нет."""
    from app import core
    from app.errors import ApiError

    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    try:
        google_targets.resolve("calendar.read")
    except ApiError as caught:
        assert caught.code == "write_target_refused"
    else:
        raise AssertionError("боевой календарь получил ключ записи")


def _draft_api(monkeypatch, draft: FakeEvents) -> None:
    """Подменённый клиент календаря: всё чтение и запись идут в `draft`."""
    from app import core

    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")
    monkeypatch.setattr(core, "_calendar_write", lambda: FakeService(draft))


def _events_api():
    from app.api import events as api
    return api


def test_отменённое_событие_не_живое(monkeypatch):
    """`status=cancelled` — события нет, хотя `events.get` его и вернул (карта Д-2)."""
    draft = FakeEvents([], event={"id": "draft-1", "status": "cancelled"})
    _draft_api(monkeypatch, draft)

    assert _events_api()._draft_event_exists("draft-1") is False
    assert draft.gets == [("draft-calendar", "draft-1")], "смотрим только черновик"


def test_подтверждённое_событие_живое(monkeypatch):
    """`status=confirmed` — событие есть: его можно править и удалять."""
    draft = FakeEvents([], event={"id": "draft-1", "status": "confirmed"})
    _draft_api(monkeypatch, draft)

    assert _events_api()._draft_event_exists("draft-1") is True


def test_предварительное_событие_живое(monkeypatch):
    """`status=tentative` — событие существует, человек лишь не подтвердил участие."""
    draft = FakeEvents([], event={"id": "draft-1", "status": "tentative"})
    _draft_api(monkeypatch, draft)

    assert _events_api()._draft_event_exists("draft-1") is True


def test_событие_без_статуса_живое(monkeypatch):
    """Нет поля `status` в ответе — считаем живым, а не наоборот."""
    draft = FakeEvents([], event={"id": "draft-1"})
    _draft_api(monkeypatch, draft)

    assert _events_api()._draft_event_exists("draft-1") is True


def test_нет_события_по_ид_значит_не_живое(monkeypatch):
    """404 от Google — события нет."""
    from tests.conftest import google_error

    draft = FakeEvents([], get_error=google_error(404))
    _draft_api(monkeypatch, draft)

    assert _events_api()._draft_event_exists("draft-1") is False


def test_отменённое_событие_повторно_не_удаляется(monkeypatch):
    """Отменённое `events.get` уже не живо: удаления в Google нет, ответ `not_bot_event`."""
    draft = FakeEvents([], event={"id": "draft-1", "status": "cancelled"})
    _draft_api(monkeypatch, draft)

    deleted, error = _events_api()._delete_calendar_event("draft-1")

    assert (deleted, error) == (False, "not_bot_event")
    assert draft.deleted == [], "повторного delete в Google быть не должно"


def test_живое_событие_удаляется(monkeypatch):
    """Контроль обратной стороны: подтверждённое событие удаляется как раньше."""
    draft = FakeEvents([], event={"id": "draft-1", "status": "confirmed"})
    _draft_api(monkeypatch, draft)

    deleted, error = _events_api()._delete_calendar_event("draft-1")

    assert (deleted, error) == (True, None)
    assert draft.deleted == [("draft-calendar", "draft-1")]


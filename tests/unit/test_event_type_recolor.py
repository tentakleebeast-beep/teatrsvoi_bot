"""Перекраска событий при сохранении типов (docs/development/190 §6.4–§6.5).

Обоснование: FR-D3, `docs/development/190` §6.5, AC-19, AC-20, AC-21 (в редакции
возврата ревью: I-7 и п. 15 тела карты — в целевом режиме `apply: "all"` отказывает
409 `recolor_target_forbidden` до сохранения). Боевой календарь есть только в
`READ_SOURCES` реестра `google_targets`; ключа записи для него не существует, и
перекраска обязана идти ключом `calendar.draft`, а не id из `read_id`.

Проверяется поведение, а не текст: какие события попали в правку, в какой
календарь ушёл вызов, что осталось при частичном сбое и что аудит записан в то же
соединение, в котором работала перекраска.
"""

from __future__ import annotations

import pytest
from app import core, event_types, google_targets
from app.errors import ApiError

from tests.conftest import google_error


class FakeRequest:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return {}


class FakeEvents:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.updated: list[tuple[str, str, dict]] = []

    def update(self, calendarId=None, eventId=None, body=None, **_kwargs):
        if self.fail_on == eventId:
            return FakeRequest(google_error(503))
        self.updated.append((calendarId, eventId, body))
        return FakeRequest()


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self) -> FakeEvents:
        return self._events


class FakeConn:
    """Соединение без БД: типы, строки `event_plan`, режим и аудит."""

    def __init__(self, mode: str = "transitional",
                 plans: list[dict] | None = None) -> None:
        self.mode = mode
        self.plans = plans or [
            {"id": 412, "kind": "show", "gcal_event_id": "ev-1"},
            {"id": 413, "kind": "rehearsal", "gcal_event_id": "ev-2"},
        ]
        self.calls: list[tuple[str, tuple]] = []
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        self.calls.append((self._sql, params or ()))
        return self

    def fetchall(self):
        low = self._sql.lower()
        if "from calendar_event_type" in low:
            return [{"code": "show", "letter": "С", "title": "Спектакль",
                     "color_id": 11, "sort_order": 20, "is_active": True},
                    {"code": "rehearsal", "letter": "Р", "title": "Репетиция",
                     "color_id": 9, "sort_order": 30, "is_active": True}]
        if "from event_plan" in low:
            kinds = self.calls[-1][1][0] if self.calls[-1][1] else []
            return [row for row in self.plans if row["kind"] in kinds]
        return []

    def fetchone(self):
        if "from app_setting" in self._sql.lower():
            return {"value": self.mode}
        return None

    def audits(self) -> list[str]:
        return [params[3] for sql, params in self.calls
                if sql.startswith("insert into audit_event") and len(params) > 3]

    def writes(self) -> list[str]:
        return [sql for sql, _params in self.calls
                if sql.lower().startswith(("update ", "insert into calendar_event_type",
                                           "delete "))]


def _ids(service: FakeService) -> list[str]:
    return [event for _calendar, event, _body in service.events().updated]


@pytest.fixture
def calendars(monkeypatch) -> tuple[str, str]:
    """Черновой и боевой id настроены: перекраска проверяет, куда именно пишет."""
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-cal")
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-cal")
    return "draft-cal", "live-cal"


def test_только_новые_не_делают_ни_одного_пишущего_вызова(calendars) -> None:
    """AC-19: `apply: "new"` сохраняет настройку и не трогает Google."""
    service = FakeService(FakeEvents())
    result = event_types.recolor(FakeConn(), ["show"], apply_mode="new",
                                 actor_id=1, write_service=service)
    assert result == {"recolored": 0, "total": 0, "failed": [], "failed_more": 0}
    assert service.events().updated == []


def test_перекрашиваются_только_изменённые_типы_в_черновом(calendars) -> None:
    """AC-19: в переходном режиме правится черновик, чужие типы не трогаются."""
    service = FakeService(FakeEvents())
    conn = FakeConn()
    result = event_types.recolor(conn, ["show"], apply_mode="all",
                                 actor_id=1, write_service=service)
    assert result["recolored"] == 1 and result["total"] == 1
    assert service.events().updated == [("draft-cal", "ev-1", {"colorId": "11"})]
    # Аудит — в том же соединении, которым работала перекраска (не в новом FakeConn):
    # иначе проверка утверждала бы «вызовов нет» о постороннем объекте.
    assert "event_types.recolor" in conn.audits(), conn.calls
    audit = next(params for sql, params in conn.calls
                 if sql.startswith("insert into audit_event")
                 and len(params) > 3 and params[3] == "event_types.recolor")
    assert '"calendar": "draft"' in str(audit[7]), audit[7]


def test_боевой_id_не_используется_даже_через_read_id(calendars, monkeypatch) -> None:
    """Граница записи: перекраска не читает id боевого календаря вовсе.

    Имя `read_id` здесь — не стиль, а проверка: прежняя реализация брала
    `read_id("calendar.read")` и передавала его в пишущий вызов, обходя реестр.
    """
    def forbidden(*_args, **_kwargs):
        raise AssertionError("перекраска обратилась к источнику чтения за адресом записи")

    monkeypatch.setattr(google_targets, "read_id", forbidden)
    service = FakeService(FakeEvents())
    result = event_types.recolor(FakeConn(), ["show"], apply_mode="all",
                                 actor_id=1, write_service=service)
    assert result["recolored"] == 1
    assert [calendar for calendar, _e, _b in service.events().updated] == ["draft-cal"]


def test_в_целевом_режиме_перекраска_отказывает_до_записи(calendars) -> None:
    """AC-21 / I-7: в `target` `apply: "all"` — 409, ноль пишущих вызовов Google."""
    service = FakeService(FakeEvents())
    conn = FakeConn(mode="target")
    with pytest.raises(ApiError) as caught:
        event_types.recolor(conn, ["show", "rehearsal"], apply_mode="all",
                            actor_id=1, write_service=service)
    assert caught.value.status_code == 409
    assert caught.value.code == "recolor_target_forbidden"
    assert service.events().updated == []
    assert conn.writes() == []
    assert "event_types.recolor" not in conn.audits()


def test_частичный_сбой_виден_числами(calendars) -> None:
    """AC-20: один отказ Google — `recolored < total`, причина в `failed`."""
    service = FakeService(FakeEvents(fail_on="ev-1"))
    result = event_types.recolor(FakeConn(), ["show", "rehearsal"], apply_mode="all",
                                 actor_id=1, write_service=service)
    assert result["total"] == 2
    assert result["recolored"] == 1
    assert result["failed"] and result["failed"][0]["gcal_event_id"] == "ev-1"


def test_повторный_прогон_не_создаёт_дублей(calendars) -> None:
    """Повтор той же настройки: тот же набор событий и тот же цвет, ничего лишнего."""
    service = FakeService(FakeEvents())
    first = event_types.recolor(FakeConn(), ["show"], apply_mode="all",
                               actor_id=1, write_service=service)
    second = event_types.recolor(FakeConn(), ["show"], apply_mode="all",
                                 actor_id=1, write_service=service)
    assert first["recolored"] == second["recolored"] == 1
    assert _ids(service) == ["ev-1", "ev-1"]


def test_пустой_список_изменённых_не_ходит_в_google(calendars) -> None:
    """Если цвет не меняли, перекраски нет даже при `apply: "all"`."""
    service = FakeService(FakeEvents())
    result = event_types.recolor(FakeConn(), [], apply_mode="all",
                                 actor_id=1, write_service=service)
    assert result["total"] == 0 and service.events().updated == []


def test_боевой_календарь_не_адресуется_ключом_записи(calendars) -> None:
    """`calendar.read` — источник чтения: `resolve` отказывает 403 (реестр цел)."""
    with pytest.raises(ApiError) as caught:
        google_targets.resolve("calendar.read")
    assert caught.value.status_code == 403
    assert caught.value.code == "write_target_refused"

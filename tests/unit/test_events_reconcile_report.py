"""Сверка календаря: шесть строк отчёта и `dry_run` без записи (190 §5.3).

Обоснование: FR-D3, `docs/development/190` §5.3 (шесть строк), AC-12…AC-15.
Отчёт — единственное, что читает экран «Сверка календаря», поэтому проверяется
смысл каждой строки: перенос виден, но черновик не удаляется; цвет и метка
считаются по настройке; «без типа» отделён от «сироты»; ошибка чтения не
выглядит свежим снимком.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app import calendar_sync, google_targets


class FakeRequest:
    def __init__(self, answer: dict | None = None) -> None:
        self.answer = answer or {}

    def execute(self) -> dict:
        return self.answer


class FakeEvents:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.deleted: list[tuple] = []

    def list(self, **_params):
        return FakeRequest({"items": self.items})

    def delete(self, calendarId=None, eventId=None, **_kwargs):
        self.deleted.append((calendarId, eventId))
        return FakeRequest({})


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self) -> FakeEvents:
        return self._events


class FakeConn:
    """Соединение без БД: типы, строки `event_plan`, настройки и журнал записей."""

    TYPES = (
        {"code": "show", "letter": "С", "title": "Спектакль", "color_id": 11,
         "sort_order": 20, "is_active": True},
        {"code": "rehearsal", "letter": "Р", "title": "Репетиция", "color_id": 9,
         "sort_order": 30, "is_active": True},
    )

    def __init__(self, plans: list[dict]) -> None:
        self.plans = plans
        self.calls: list[tuple[str, tuple]] = []
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        self.calls.append((self._sql, params or ()))
        return self

    def fetchall(self):
        low = self._sql.lower()
        if "from calendar_event_type" in low:
            return [dict(item) for item in self.TYPES]
        if "from event_plan" in low:
            return self.plans
        return []

    def fetchone(self):
        return None

    def writes(self) -> list[str]:
        return [sql for sql, _ in self.calls
                if sql.startswith(("update ", "insert ", "delete "))]


START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)

PLANS = [
    # черновик уже убран прошлой сверкой: переносом он больше не считается
    {"id": 1, "status": "confirmed", "gcal_event_id": None, "gcal_calendar_id": "read",
     "title": "Жизнь насекомых", "kind": "show", "starts_at": datetime(2026, 11, 24, 15, tzinfo=UTC),
     "ends_at": datetime(2026, 11, 24, 18, tzinfo=UTC), "location": "stage"},
    {"id": 2, "status": "confirmed", "gcal_event_id": "ev-2", "gcal_calendar_id": "draft",
     "title": "Чёрная пурга", "kind": "rehearsal",
     "starts_at": datetime(2026, 11, 25, 12, tzinfo=UTC),
     "ends_at": datetime(2026, 11, 25, 14, tzinfo=UTC), "location": "stage"},
]

ITEMS = [
    # событие бота: цвет не по типу (8 вместо 11), метка другого типа
    {"id": "ev-1", "summary": "[Р] Жизнь насекомых", "colorId": "8",
     "status": "confirmed", "start": {"dateTime": "2026-11-24T15:00:00Z"},
     "end": {"dateTime": "2026-11-24T18:00:00Z"},
     "extendedProperties": {"private": {"svoi_event_id": "1", "svoi_kind": "show"}}},
    # перенос: строка нашлась маркером, но черновик ещё в черновом
    {"id": "ev-2", "summary": "[Р] Чёрная пурга", "colorId": "9",
     "status": "confirmed", "start": {"dateTime": "2026-11-25T12:00:00Z"},
     "end": {"dateTime": "2026-11-25T14:00:00Z"},
     "extendedProperties": {"private": {"svoi_event_id": "2", "svoi_kind": "rehearsal"}},
     "updated": "2026-11-20T10:00:00Z"},
    # людское событие без типа
    {"id": "ev-3", "summary": "Аренда зала", "status": "confirmed",
     "start": {"dateTime": "2026-11-26T10:00:00Z"},
     "end": {"dateTime": "2026-11-26T12:00:00Z"}, "extendedProperties": {"private": {}}},
    # маркер бота без строки в базе — сирота
    {"id": "ev-4", "summary": "[С] Пропавшая строка", "status": "confirmed",
     "start": {"dateTime": "2026-11-27T10:00:00Z"},
     "end": {"dateTime": "2026-11-27T12:00:00Z"},
     "extendedProperties": {"private": {"svoi_event_id": "99", "svoi_kind": "show"}}},
]


def _scan(monkeypatch, *, dry_run: bool = True):
    monkeypatch.setattr(google_targets, "read_id", lambda key: "live-cal")
    conn = FakeConn(PLANS)
    service = FakeService(FakeEvents(ITEMS))
    report = calendar_sync.scan(conn, read_service=service, draft_service=service,
                                time_from=START, time_to=END, dry_run=dry_run)
    return conn, service, report


def test_шесть_строк_на_месте(monkeypatch) -> None:
    """§5.3: у отчёта ровно шесть категорий и счётчики; массив сироты не отдельная строка."""
    _conn, _service, report = _scan(monkeypatch)
    for key in ("changed_in_google", "moved_to_live", "color_mismatch",
                "label_mismatch", "orphan_marker", "untyped", "counts"):
        assert key in report, key
    assert report["error"] is None and report["stale"] is False
    assert report["scanned"] == len(ITEMS)


def test_цвет_и_метка_считаются_по_настройке(monkeypatch) -> None:
    """AC-13: `kind=show` с цветом 8 — в `color_mismatch` с ожидаемым 11."""
    _conn, _service, report = _scan(monkeypatch)
    color = next(item for item in report["color_mismatch"] if item["plan_id"] == 1)
    assert color["color_id"] == "8" and color["expected_color_id"] == 11
    label = next(item for item in report["label_mismatch"] if item["plan_id"] == 1)
    assert label["letter"] == "Р" and label["expected_letter"] == "С"


def test_перенос_виден_а_черновик_не_удаляется(monkeypatch) -> None:
    """AC-12: `dry_run` показывает перенос и не трогает черновик."""
    conn, service, report = _scan(monkeypatch)
    assert {item["plan_id"] for item in report["moved_to_live"]} == {2}
    assert service.events().deleted == [], "экран сверки удалил черновик"
    assert conn.writes() == [], "экран сверки записал в базу"


def test_без_типа_и_сирота_разные_строки(monkeypatch) -> None:
    """AC-14: людское без метки — «без типа», маркер без строки — сирота."""
    _conn, _service, report = _scan(monkeypatch)
    assert report["untyped"] == {"total": 1, "human": 1}
    assert {item["plan_id"] for item in report["orphan_marker"]} == {99}
    assert all(item.get("plan_id") != 99 for item in report["color_mismatch"])


def test_недоступный_календарь_не_выдаёт_себя_за_свежий(monkeypatch) -> None:
    """AC-15: ошибка чтения — `error` и `stale: true`, прошлый снимок не подставляется."""
    monkeypatch.setattr(google_targets, "read_id", lambda key: "live-cal")
    conn = FakeConn(PLANS)

    class Broken(FakeService):
        def events(self):
            return self

        def list(self, **_params):
            raise OSError("network down")

    report = calendar_sync.scan(conn, read_service=Broken(FakeEvents([])),
                                draft_service=Broken(FakeEvents([])),
                                time_from=START, time_to=END, dry_run=True)
    assert report["error"] in ("calendar_unavailable", "no_access")
    assert report["stale"] is True
    assert report["changed_in_google"] == [] and report["counts"] == {}

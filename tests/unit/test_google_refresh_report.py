"""Сверка календаря: отчёт из шести строк экрана «Сверить» (190 §5.3, §10).

Обоснование: docs/development/190 §10.2, AC-11…AC-15; docs/development/186 §5, §7.
Экран E5 читает ровно этот блок, поэтому проверяется не «есть ключ», а смысл:
`dry_run` не пишет ничего (AC-11), перенос не удаляет боевое (AC-12), цвет и метка
считаются по настройке (AC-13), «без типа» отделён от «сироты» (AC-14), а неудачное
чтение не выглядит свежим (AC-15, AC-17).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app import calendar_sync, core
from app.errors import ApiError

START = datetime(2026, 12, 19, 18, 0, tzinfo=UTC)
END = datetime(2026, 12, 19, 21, 0, tzinfo=UTC)


class FakeConn:
    """Соединение без БД: планы событий, настройки и журнал запросов."""

    def __init__(self, plans: list[dict] | None = None,
                 settings: dict[str, str] | None = None) -> None:
        self.plans = plans or []
        self.settings = settings or {}
        self.calls: list[tuple[str, tuple]] = []
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        self.calls.append((self._sql, params or ()))
        return self

    def fetchall(self):
        return self.plans if "from event_plan" in self._sql.lower() else []

    def fetchone(self):
        low = self._sql.lower()
        if "from app_setting" in low:
            params = self.calls[-1][1]
            key = params[0] if params else None
            value = self.settings.get(key) if key is not None else None
            return {"value": value} if value is not None else None
        return None

    def writes(self) -> list[str]:
        return [sql for sql, _ in self.calls
                if sql.startswith(("update ", "insert ", "delete "))]


class FakeRequest:
    def __init__(self, answer: dict | None = None, error: Exception | None = None) -> None:
        self.answer = answer or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.answer


class FakeEvents:
    def __init__(self, items: list[dict], error: Exception | None = None) -> None:
        self.items = items
        self.error = error
        self.deleted: list[tuple] = []
        self.updated: list[tuple] = []
        self.inserts: list[dict] = []

    def list(self, **_params):
        return FakeRequest({"items": self.items}, self.error)

    def delete(self, calendarId=None, eventId=None, **_kwargs):
        self.deleted.append((calendarId, eventId))
        return FakeRequest({})

    def update(self, calendarId=None, eventId=None, body=None, **_kwargs):
        self.updated.append((calendarId, eventId))
        return FakeRequest({"id": eventId})

    def insert(self, calendarId=None, body=None, **_kwargs):
        self.inserts.append({"calendar": calendarId, "body": body})
        return FakeRequest({"id": "new"})


class FakeService:
    def __init__(self, events: FakeEvents) -> None:
        self._events = events

    def events(self):
        return self._events


def _plan(plan_id: int, *, kind: str = "show", title: str = "Спектакль",
          start: datetime = START, calendar: str = "draft-cal",
          gcal: str | None = "draft-1", location: str | None = None) -> dict:
    return {"id": plan_id, "status": "planned", "gcal_event_id": gcal,
            "gcal_calendar_id": calendar, "title": title, "kind": kind,
            "starts_at": start, "ends_at": END, "location": location}


def _event(plan_id: int | None, *, summary: str, color: str | None = "11",
           start: datetime = START, location: str | None = None,
           status: str = "confirmed", event_id: str = "live-1",
           kind: str = "show") -> dict:
    private = {"svoi_event_id": str(plan_id), "svoi_kind": kind} if plan_id else {}
    return {"id": event_id, "summary": summary, "status": status,
            "colorId": color, "location": location,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": END.isoformat()},
            "updated": START.isoformat(),
            "extendedProperties": {"private": private}}


def _scan(monkeypatch, conn: FakeConn, items: list[dict], draft: FakeEvents,
          *, dry_run: bool = False, error: Exception | None = None) -> dict:
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")
    return calendar_sync.scan(conn, read_service=FakeService(FakeEvents(items, error)),
                              draft_service=FakeService(draft),
                              time_from=START, time_to=END, dry_run=dry_run)


def _fixture(monkeypatch) -> tuple[FakeConn, FakeEvents, dict]:
    """Отчёт по всем категориям сразу: цвет, метка, перенос, сирота, «без типа»."""
    conn = FakeConn([
        _plan(1, kind="show", title="Жизнь насекомых", location="Сцена"),
        _plan(2, kind="rehearsal", title="Чёрная пурга", gcal="draft-2",
              start=datetime(2026, 12, 19, 20, 0, tzinfo=UTC)),
        _plan(3, kind="show", title="Уже перенесён", calendar=calendar_sync.LIVE_MARK,
              gcal=None),
    ])
    draft = FakeEvents([])
    items = [
        # цвет не по типу: show → 11, у события 8
        _event(1, summary="[С] Жизнь насекомых", color="8", location="Сцена",
               event_id="live-1"),
        # метка не по типу и время в Google разошлось с базой: rehearsal → Р
        _event(2, summary="[Т] Чёрная пурга", color="9", event_id="live-2", kind="rehearsal"),
        # маркер бота без строки в базе
        _event(99, summary="[С] Потерянное", event_id="live-99"),
        # людское событие без метки и без svoi_kind
        _event(None, summary="Встреча с бухгалтером", event_id="human-1"),
        # людское событие с меткой: типизировано, в «без типа» не попадает
        _event(None, summary="[А] Аренда зала", event_id="human-2"),
    ]
    report = _scan(monkeypatch, conn, items, draft)
    return conn, draft, report


def test_отчёт_отдаёт_все_ключи_контракта(monkeypatch):
    """190 §10.2: экран E5 читает блок «calendar.read» целиком, без второго запроса."""
    _conn, _draft, report = _fixture(monkeypatch)
    for key in ("calendar", "window", "as_of", "stale", "error", "scanned",
                "changed_in_google", "moved_to_live", "color_mismatch",
                "label_mismatch", "orphan_marker", "untyped", "counts"):
        assert key in report, f"в отчёте нет {key}"
    for key in ("changed_in_google", "moved_to_live", "color_mismatch",
                "label_mismatch", "orphan_marker"):
        assert f"{key}_more" in report
    assert report["error"] is None and report["stale"] is False
    assert report["scanned"] == 5
    assert isinstance(report["untyped"], dict)
    assert set(report["untyped"]) == {"total", "human"}


def test_окно_отчёта_включает_последний_день(monkeypatch):
    """Одно правило с `refresh`: границы окна в отчёте включающие (190 §10.2, §10.1)."""
    _conn, _draft, report = _fixture(monkeypatch)

    assert report["window"] == {"from": "2026-12-19", "to": "2026-12-19"}


def test_цвет_и_метка_считаются_по_настройке(monkeypatch):
    """AC-13: цвет show=11, метка show=С — расхождение видно поимённо."""
    _conn, _draft, report = _fixture(monkeypatch)
    color = next(item for item in report["color_mismatch"] if item["plan_id"] == 1)
    assert color["color_id"] == "8" and color["expected_color_id"] == 11
    assert color["kind"] == "show"
    label = next(item for item in report["label_mismatch"] if item["plan_id"] == 2)
    assert label["letter"] == "Т" and label["expected_letter"] == "Р"
    assert label["kind"] == "rehearsal"
    assert all(item["plan_id"] != 3 for item in report["label_mismatch"])


def test_без_типа_и_сирота_разные_строки(monkeypatch):
    """AC-14: людское без метки — «без типа»; маркер без строки — сирота."""
    _conn, _draft, report = _fixture(monkeypatch)
    assert report["untyped"] == {"total": 1, "human": 1}
    assert [item["svoi_event_id"] for item in report["orphan_marker"]] == ["99"]
    assert all(item.get("plan_id") != 99 for item in report["color_mismatch"])


def test_перенос_виден_и_боевое_не_удаляется(monkeypatch):
    """AC-12: перенос в отчёте, удаляется только черновик."""
    conn, draft, report = _fixture(monkeypatch)
    moved = {item["plan_id"] for item in report["moved_to_live"]}
    assert moved == {1, 2}
    item = next(entry for entry in report["moved_to_live"] if entry["plan_id"] == 1)
    assert item["draft_event_id"] == "draft-1" and item["svoi_event_id"] == "1"
    assert draft.deleted == [("draft-calendar", "draft-1"), ("draft-calendar", "draft-2")]
    assert any("gcal_calendar_id" in sql for sql in conn.writes())


def test_изменения_в_google_считаются_по_полям(monkeypatch):
    """Строка «Изменено в Google»: только разошедшиеся поля, с обеими сторонами."""
    _conn, _draft, report = _fixture(monkeypatch)
    entry = next(item for item in report["changed_in_google"] if item["plan_id"] == 2)
    assert entry["start_google"].startswith("2026-12-19T18:00")
    assert entry["start_db"].startswith("2026-12-19T20:00")
    assert "title_google" not in entry, "одинаковые названия не считаются изменением"
    assert all(item["plan_id"] != 1 for item in report["changed_in_google"]), \
        "совпавшее событие не должно попадать в «изменено в Google»"


def test_dry_run_не_делает_ни_одной_записи(monkeypatch):
    """AC-11: «Сверить» с `dry_run` не пишет ни в базу, ни в календарь, ни в аудит."""
    conn = FakeConn([_plan(1)])
    draft = FakeEvents([])
    report = _scan(monkeypatch, conn, [_event(1, summary="[С] Спектакль")], draft,
                   dry_run=True)
    assert report["moved_to_live"] and report["dry_run"] is True
    assert draft.deleted == []
    assert conn.writes() == [], f"dry_run писал в базу: {conn.writes()}"


def test_неудачное_чтение_не_помечается_свежим(monkeypatch):
    """AC-15/AC-17: ошибка Google — `error`, `stale`, и отметка чтения не ставится."""
    conn = FakeConn(settings={calendar_sync.CALENDAR_READ_KEY: "2026-12-01T10:00:00+00:00"})
    report = _scan(monkeypatch, conn, [], FakeEvents([]),
                   error=ApiError(502, "календарь недоступен", code="calendar_unavailable"))
    assert report["error"] == "calendar_unavailable"
    assert report["stale"] is True
    assert report["as_of"].startswith("2026-12-01"), \
        "as_of при ошибке — время последнего удачного чтения, а не «сейчас»"
    assert not [sql for sql in conn.writes() if "app_setting" in sql]


def test_отметка_чтения_пишется_после_успеха(monkeypatch):
    """Успешное чтение обновляет `read_at` и `last_edit` — по ним живёт freshness."""
    conn = FakeConn([_plan(1)])
    _scan(monkeypatch, conn, [_event(1, summary="[С] Спектакль")], FakeEvents([]))
    writes = [sql for sql in conn.writes() if "app_setting" in sql]
    assert len(writes) == 2, writes
    assert any(calendar_sync.CALENDAR_EDIT_KEY in str(params) for _sql, params in conn.calls)


def test_повторная_сверка_не_зовёт_перенос_сиротой(monkeypatch):
    """После чистки черновика строка остаётся найденной: это не «сирота»."""
    conn = FakeConn([_plan(1, calendar=calendar_sync.LIVE_MARK, gcal=None)])
    report = _scan(monkeypatch, conn, [_event(1, summary="[С] Спектакль")], FakeEvents([]))
    assert report["orphan_marker"] == []
    assert report["moved_to_live"] == []


def test_боевой_без_идентификатора_это_календарь_не_настроен(monkeypatch):
    """190 §5.4: пустой id боевого календаря — `calendar_id_missing`, не запись."""
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "")
    report = calendar_sync.scan(FakeConn(), read_service=FakeService(FakeEvents([])),
                                draft_service=FakeService(FakeEvents([])),
                                time_from=START, time_to=END)
    assert report["error"] == "calendar_id_missing" and report["stale"] is True

"""Сверка боевого календаря и черновика: переносы человеком, отмены, свежесть.

Зачем (docs/development/186 §5.3 и §7). Человек переносит черновик в боевой календарь
руками, а отменяет событие — уже в боевом. Бот не угадывает это по названию и времени:
он ищет событие по маркеру `svoi_event_id`, который сам же и записал.

Правила:

* нашёл маркер в боевом — удаляет **только черновик** и пишет аудит; боевое не трогает;
* `status = cancelled` у боевого события с нашим маркером — в базе `cancelled`;
* отсутствие события в боевом отменой не считается;
* фоновой синхронизации нет: `scan` вызывается из `POST /tools/google/refresh`.

Тесты: `tests/unit/test_calendar_draft_only.py`, `tests/unit/test_google_freshness.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app import core, google_targets, sheet_binding
from app.calendar_blocks import PROP_EVENT_ID
from app.errors import ApiError
from app.external import GOOGLE_API_ERRORS

#: Поля события, которые нужны сверке: без них не отличить отмену от правки.
EVENT_FIELDS = "nextPageToken,items(id,summary,start,end,colorId,status,extendedProperties,htmlLink)"

#: «Свежими» считаются данные не старше этого окна (186 §7: экран показывает «данные от…»).
STALE_AFTER_MINUTES = 15

#: Префикс ключа настроек, где лежит время последнего чтения календаря.
CALENDAR_READ_KEY = "google.calendar.read_at"


def default_window(today: date | None = None) -> tuple[datetime, datetime]:
    """Окно чтения по умолчанию — текущий календарный месяц в зоне сервиса."""
    now = datetime.now(core.TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, next_month


def read_window(service, calendar_id: str, time_from: datetime, time_to: datetime,
                *, private_extended_property: str | None = None) -> list[dict]:
    """События окна по страницам (`nextPageToken`), а не одной страницей `maxResults`.

    Окно месяца бывает больше страницы: одним запросом его не прочитать
    (уточнение E5-3, docs/development/188 §10).
    """
    items: list[dict] = []
    page: str | None = None
    while True:
        params: dict = {"calendarId": calendar_id, "timeMin": time_from.isoformat(),
                        "timeMax": time_to.isoformat(), "singleEvents": True,
                        "orderBy": "startTime", "maxResults": 500,
                        "fields": EVENT_FIELDS, "showDeleted": True}
        if private_extended_property:
            params["privateExtendedProperty"] = private_extended_property
        if page:
            params["pageToken"] = page
        try:
            answer = service.events().list(**params).execute()
        except GOOGLE_API_ERRORS as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            code = "no_access" if status in (401, 403) else "calendar_unavailable"
            text = ("нет доступа к календарю" if code == "no_access"
                    else "календарь недоступен")
            raise ApiError(403 if code == "no_access" else 502, text, code=code) from None
        items.extend(answer.get("items") or [])
        page = answer.get("nextPageToken")
        if not page:
            return items


def _private(event: dict) -> dict:
    return ((event.get("extendedProperties") or {}).get("private") or {})


def _event_id(event: dict) -> int | None:
    raw = str(_private(event).get(PROP_EVENT_ID) or "").strip()
    return int(raw) if raw.isdigit() else None


def mark_read(conn) -> None:
    """Отметка «календарь читали в …»: по ней `freshness` отличает свежее от старого."""
    conn.execute("""insert into app_setting (key, value) values (%s, %s)
                    on conflict (key) do update set value = excluded.value""",
                 (CALENDAR_READ_KEY, datetime.now(UTC).isoformat()))


def read_as_of(conn) -> datetime | None:
    row = conn.execute("select value from app_setting where key = %s",
                       (CALENDAR_READ_KEY,)).fetchone()
    if row is None:
        return None
    raw = row["value"] if isinstance(row, dict) else row[0]
    try:
        parsed = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def scan(conn, *, read_service, draft_service, time_from: datetime, time_to: datetime,
         dry_run: bool = False) -> dict:
    """Сверить боевой календарь с базой: переносы, отмены, чужие маркеры.

    `dry_run=True` — ничего не пишем: ни черновик, ни базу, ни аудит. Дизайн 187 §4
    требует, чтобы «Сверить» ничего не меняло, а 186 §5.3 чистит черновик; `dry_run`
    выполняет оба правила, не выбирая между ними.
    """
    read_id = google_targets.read_id("calendar.read")
    report: dict = {"calendar": "read", "window": {"from": time_from.date().isoformat(),
                                                   "to": time_to.date().isoformat()},
                    "as_of": datetime.now(UTC).isoformat(), "scanned": 0,
                    "moved_to_live": [], "cancelled_in_live": [], "orphan_marker": [],
                    "counts": {}, "dry_run": dry_run, "error": None}
    if not read_id:
        report["error"] = "write_target_unset"
        return report

    try:
        live = read_window(read_service, read_id, time_from, time_to)
    except ApiError as exc:
        report["error"] = exc.code
        return report
    report["scanned"] = len(live)

    plans = {row["id"]: row for row in conn.execute(
        "select id, status, gcal_event_id from event_plan where gcal_event_id is not null"
    ).fetchall()}

    for event in live:
        plan_id = _event_id(event)
        if plan_id is None:
            continue
        item = {"plan_id": plan_id, "gcal_event_id": event.get("id"),
                "svoi_event_id": str(plan_id), "title_google": event.get("summary"),
                "status_google": event.get("status")}
        plan = plans.get(plan_id)
        if plan is None:
            report["orphan_marker"].append(item)
            continue
        if str(event.get("status") or "") == "cancelled":
            item["start_google"] = event.get("start")
            report["cancelled_in_live"].append(item)
            if not dry_run and plan["status"] != "cancelled":
                conn.execute("update event_plan set status = 'cancelled' where id = %s",
                             (plan_id,))
                core.audit(conn, "google", None, "event.cancelled_in_google", "event_plan",
                           str(plan_id), before={"status": plan["status"]},
                           after={"status": "cancelled",
                                  "gcal_event_id": event.get("id")})
            continue
        # событие с нашим маркером уже в боевом — значит человек перенёс черновик
        report["moved_to_live"].append(item)
        if not dry_run and plan["gcal_event_id"]:
            _clean_draft(conn, draft_service, plan, item)

    report["counts"] = {key: len(report[key]) for key in
                        ("moved_to_live", "cancelled_in_live", "orphan_marker")}
    if not dry_run:
        mark_read(conn)
    return report


def _clean_draft(conn, draft_service, plan: dict, item: dict) -> None:
    """Удалить **черновик** после переноса человеком. Боевое событие не трогаем."""
    draft_id = google_targets.resolve("calendar.draft")
    draft_event_id = plan["gcal_event_id"]
    deleted = False
    try:
        draft_service.events().delete(calendarId=draft_id, eventId=draft_event_id).execute()
        deleted = True
    except GOOGLE_API_ERRORS:
        # черновика уже нет (человек удалил при переносе) — это ожидаемый исход
        deleted = False
    conn.execute("update event_plan set gcal_event_id = null, gcal_calendar_id = null "
                 "where id = %s", (plan["id"],))
    core.audit(conn, "google", None, "event.draft_cleaned", "event_plan", str(plan["id"]),
               before={"gcal_event_id": draft_event_id},
               after={"draft_deleted": deleted, "live_event_id": item.get("gcal_event_id"),
                      "calendar": "read"})


def freshness(conn) -> list[dict]:
    """Свежесть источников: `as_of`, `stale`, `error` (186 §7).

    Значения берутся из `sheet_binding` (последнее чтение/правка файла) и из отметки
    чтения календаря. Прошлый снимок никогда не выдаётся за свежий — это и есть `stale`.
    """
    now = datetime.now(UTC)
    out: list[dict] = []
    for key in ("schedule.human", "budget.live", "schedule.workload", "schedule.test",
                "calendar.read", "drive.root"):
        entry = {"key": key, "as_of": None, "last_edit_time": None, "stale": True,
                 "error": None}
        if key == "calendar.read":
            as_of = read_as_of(conn)
            entry["as_of"] = as_of.isoformat() if as_of else None
            entry["stale"] = (as_of is None
                              or now - as_of > timedelta(minutes=STALE_AFTER_MINUTES))
        else:
            binding = sheet_binding.load_binding(conn, key)
            if binding is None:
                entry["error"] = "binding_missing"
            else:
                last_read, last_edit = binding.last_read_at, binding.last_edit_time
                entry["as_of"] = last_read.isoformat() if last_read else None
                entry["last_edit_time"] = last_edit.isoformat() if last_edit else None
                entry["stale"] = (last_read is None
                                  or now - last_read > timedelta(minutes=STALE_AFTER_MINUTES))
        out.append(entry)
    return out

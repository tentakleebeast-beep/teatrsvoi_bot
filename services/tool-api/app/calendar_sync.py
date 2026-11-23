"""Сверка боевого календаря и черновика: переносы человеком, отмены, свежесть.

Зачем (docs/development/186 §5.3 и §7, docs/development/190 §10). Человек переносит
черновик в боевой календарь руками, а отменяет событие — уже в боевом. Бот не угадывает
это по названию и времени: он ищет событие по маркеру `svoi_event_id`, который сам же и
записал.

Правила:

* нашёл маркер в боевом — удаляет **только черновик** и пишет аудит; боевое не трогает;
* `status = cancelled` у боевого события с нашим маркером — в базе `cancelled`;
* отсутствие события в боевом отменой не считается;
* фоновой синхронизации нет: `scan` вызывается из `POST /tools/google/refresh`.

Отчёт `scan` — это шесть строк экрана «Сверить» (190 §5.3): `changed_in_google`,
`moved_to_live`, `color_mismatch`, `label_mismatch`, `untyped` и `error`. Каждая
категория отдаёт не больше `ITEMS_LIMIT` элементов, остальное считается в `*_more`.

Про `dry_run`: при `True` не выполняется **ни одной** записи — ни черновик, ни база, ни
аудит, ни отметка чтения (190 §10.1). «Сверить» на экране всегда присылает `dry_run`.

Тесты: `tests/unit/test_calendar_draft_only.py`, `tests/unit/test_google_freshness.py`,
`tests/unit/test_google_refresh_report.py`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from app import core, google_targets, sheet_binding
from app.calendar_blocks import PROP_EVENT_ID, PROP_KIND
from app.errors import ApiError
from app.external import GOOGLE_API_ERRORS

#: Поля события, которые нужны сверке: без них не отличить отмену от правки.
EVENT_FIELDS = ("nextPageToken,items(id,summary,start,end,colorId,status,updated,"
                "location,extendedProperties,htmlLink)")

#: «Свежими» считаются данные не старше этого окна (186 §7: экран показывает «данные от…»).
STALE_AFTER_MINUTES = 15

#: Префикс ключа настроек, где лежит время последнего чтения календаря.
CALENDAR_READ_KEY = "google.calendar.read_at"
#: Время последней правки в самом Google (`updated` последнего события окна, 186 §3).
CALENDAR_EDIT_KEY = "google.calendar.last_edit"
#: Отметка чтения корня Drive: у Drive нет строки `sheet_binding`, а свежесть нужна.
DRIVE_READ_KEY = "google.drive.read_at"

#: Значение `event_plan.gcal_calendar_id`, которым помечается «черновик уже убран,
#: событие живёт в боевом». Без этой метки повторная сверка искала строку по пустому
#: `gcal_event_id` и объявляла настоящий перенос «сиротой».
LIVE_MARK = "read"

#: Предел элементов в категории отчёта (190 §10.2): остальное — в `<категория>_more`.
ITEMS_LIMIT = 50

#: Категории отчёта сверки — ровно строки экрана «Сверить» (190 §5.3).
CATEGORIES = ("changed_in_google", "moved_to_live", "color_mismatch",
              "label_mismatch", "orphan_marker")

#: Буква типа события в заголовке `[С] Название` → код типа. До таблицы
#: `calendar_event_type` (её создаёт карта E5) это единственный источник букв.
#: shortcut: литеральные буквы типов, апгрейд — таблица типов из 190 §7 при переходе E5.
TYPE_LETTERS = {"rental": "А", "show": "С", "rehearsal": "Р", "training": "Т",
                "project": "П"}

#: Разбор метки: `[С] ` в начале заголовка. Метка снимается одна (190 §3.2).
LABEL_RE = re.compile(r"^\s*\[([А-ЯЁ])\]\s*")

#: Старый формат заголовка «Тип - Название»: бот писал так до появления меток.
LEGACY_SEPS = (" - ", " — ", " – ", "- ")


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


def event_label(summary: str | None) -> str | None:
    """Метка типа из заголовка (`[С] Название` → `С`). Нет метки — `None`."""
    found = LABEL_RE.match(str(summary or ""))
    return found.group(1) if found else None


def plain_title(summary: str | None) -> str:
    """Заголовок без метки типа и без старого префикса «Тип - » (190 §3.2)."""
    text = LABEL_RE.sub("", str(summary or "").strip()).strip()
    for title in sorted(set(core.KIND_TITLES_RU.values()), key=len, reverse=True):
        for sep in LEGACY_SEPS:
            if text.startswith(f"{title}{sep}"):
                return text[len(title) + len(sep):].strip()
    return text


def kind_by_letter(letter: str | None) -> str | None:
    """Код типа по букве метки: обратный словарь `TYPE_LETTERS`."""
    for code, mark in TYPE_LETTERS.items():
        if mark == letter:
            return code
    return None


def _moment(event: dict, edge: str) -> tuple[datetime | None, bool]:
    """Начало/конец события: `dateTime` или `date` (событие на весь день)."""
    part = event.get(edge) or {}
    raw = part.get("dateTime")
    if raw:
        return _to_dt(raw), False
    return _to_dt(part.get("date")), True


def _to_dt(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _same_moment(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.astimezone(UTC) == right.astimezone(UTC)


def _show_moment(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat(timespec="seconds") if value else None


def _setting(conn, key: str) -> str:
    row = conn.execute("select value from app_setting where key = %s", (key,)).fetchone()
    if row is None:
        return ""
    try:
        return str(row["value"] or "")
    except (TypeError, KeyError, IndexError):
        return str(row[0] or "") if len(row) else ""


def _put_setting(conn, key: str, value: str) -> None:
    conn.execute("""insert into app_setting (key, value) values (%s, %s)
                    on conflict (key) do update set value = excluded.value""", (key, value))


def mark_read(conn, last_edit: datetime | None = None) -> None:
    """Отметка «календарь читали в …»: по ней `freshness` отличает свежее от старого.

    `last_edit` — время правки в Google (`updated` последнего события окна). Пишется
    только после успешного чтения: отметка о неудачной попытке выдала бы прошлый
    снимок за свежий (AC-17).
    """
    _put_setting(conn, CALENDAR_READ_KEY, datetime.now(UTC).isoformat())
    if last_edit is not None:
        _put_setting(conn, CALENDAR_EDIT_KEY, last_edit.astimezone(UTC).isoformat())


def _parse_setting(conn, key: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_setting(conn, key))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def mark_source_read(conn, key: str) -> None:
    """Отметка «источник читали в …» для источников без строки `sheet_binding`."""
    _put_setting(conn, key, datetime.now(UTC).isoformat())


def read_as_of(conn) -> datetime | None:
    return _parse_setting(conn, CALENDAR_READ_KEY)


def read_last_edit(conn) -> datetime | None:
    return _parse_setting(conn, CALENDAR_EDIT_KEY)


def _add(report: dict, key: str, item: dict) -> None:
    """Положить элемент в категорию, считая полное число для `counts` и `*_more`."""
    report["totals"][key] += 1
    bucket = report[key]
    if len(bucket) < ITEMS_LIMIT:
        bucket.append(item)
    else:
        report[f"{key}_more"] += 1


def _changes(event: dict, plan: dict) -> dict:
    """Расхождения Google ↔ база по одному событию (строка «Изменено в Google»)."""
    item: dict = {"plan_id": plan.get("id"), "gcal_event_id": event.get("id"),
                  "svoi_event_id": str(plan.get("id"))}
    title_google = plain_title(event.get("summary"))
    title_db = str(plan.get("title") or "").strip()
    if title_google != title_db:
        item["title_google"], item["title_db"] = title_google, title_db
    start_google, _all_day = _moment(event, "start")
    start_db = _to_dt(plan.get("starts_at"))
    if not _same_moment(start_google, start_db):
        item["start_google"] = _show_moment(start_google)
        item["start_db"] = _show_moment(start_db)
    location_google = str(event.get("location") or "").strip()
    location_db = str(plan.get("location") or "").strip()
    if location_google != location_db:
        item["location_google"], item["location_db"] = location_google, location_db
    return item if len(item) > 3 else {}


def _live_plans(conn) -> dict[int, dict]:
    rows = conn.execute("""
        select id, status, gcal_event_id, gcal_calendar_id, title, kind,
               starts_at, ends_at, location
        from event_plan
        where gcal_event_id is not null or gcal_calendar_id = %s""",
        (LIVE_MARK,)).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def scan(conn, *, read_service, draft_service, time_from: datetime, time_to: datetime,
         dry_run: bool = False) -> dict:
    """Сверить боевой календарь с базой: переносы, отмены, цвета, метки, сироты.

    `dry_run=True` — ничего не пишем: ни черновик, ни базу, ни аудит, ни отметку чтения.
    Дизайн 187 §4 требует, чтобы «Сверить» ничего не меняло, а 186 §5.3 чистит
    черновик; `dry_run` выполняет оба правила, не выбирая между ними.
    """
    read_id = google_targets.read_id("calendar.read")
    previous = read_as_of(conn)
    report: dict = {
        "calendar": "read", "window": {"from": time_from.date().isoformat(),
                                       "to": time_to.date().isoformat()},
        "as_of": None, "stale": True, "error": None, "scanned": 0,
        "untyped": {"total": 0, "human": 0}, "counts": {}, "dry_run": dry_run,
        "changed_in_google": [], "moved_to_live": [], "color_mismatch": [],
        "label_mismatch": [], "orphan_marker": [],
        "totals": {key: 0 for key in CATEGORIES},
    }
    for key in CATEGORIES:
        report[f"{key}_more"] = 0

    if not read_id:
        report["error"] = "calendar_id_missing"
        report["as_of"] = previous.isoformat() if previous else None
        return report

    try:
        live = read_window(read_service, read_id, time_from, time_to)
    except ApiError as exc:
        report["error"] = exc.code
        report["as_of"] = previous.isoformat() if previous else None
        return report

    report["scanned"] = len(live)
    report["as_of"] = datetime.now(UTC).isoformat()
    report["stale"] = False

    plans = _live_plans(conn)
    last_edit: datetime | None = None

    for event in live:
        updated = _to_dt(event.get("updated"))
        if updated and (last_edit is None or updated > last_edit):
            last_edit = updated

        plan_id = _event_id(event)
        private = _private(event)
        summary = event.get("summary")
        letter = event_label(summary)
        kind = str(private.get(PROP_KIND) or "").strip().lower()

        if plan_id is None:
            # людское событие: бот его не трогает, но «без типа» показать обязан
            if not kind and not letter:
                report["untyped"]["total"] += 1
                report["untyped"]["human"] += 1
            continue

        plan = plans.get(plan_id)
        if plan is None:
            _add(report, "orphan_marker", {
                "plan_id": plan_id, "gcal_event_id": event.get("id"),
                "svoi_event_id": str(plan_id), "title_google": summary,
                "start_google": _show_moment(_moment(event, "start")[0])})
            continue

        if str(event.get("status") or "") == "cancelled":
            report["totals"]["cancelled_in_live"] = \
                report["totals"].get("cancelled_in_live", 0) + 1
            if not dry_run and plan.get("status") != "cancelled":
                conn.execute("update event_plan set status = 'cancelled' where id = %s",
                             (plan_id,))
                core.audit(conn, "google", None, "event.cancelled_in_google", "event_plan",
                           str(plan_id), before={"status": plan.get("status")},
                           after={"status": "cancelled", "gcal_event_id": event.get("id")})
            continue

        event_kind = kind or kind_by_letter(letter) or str(plan.get("kind") or "")
        if not event_kind and not letter:
            report["untyped"]["total"] += 1

        expected_letter = TYPE_LETTERS.get(event_kind)
        if event_kind and expected_letter and letter != expected_letter:
            _add(report, "label_mismatch", {
                "plan_id": plan_id, "gcal_event_id": event.get("id"),
                "svoi_event_id": str(plan_id), "title_google": summary,
                "letter": letter, "expected_letter": expected_letter, "kind": event_kind})

        expected_color = core.EVENT_COLORS.get(event_kind)
        color_id = event.get("colorId")
        if event_kind and expected_color is not None and str(color_id or "") != str(expected_color):
            _add(report, "color_mismatch", {
                "plan_id": plan_id, "gcal_event_id": event.get("id"),
                "svoi_event_id": str(plan_id), "kind": event_kind,
                "color_id": color_id, "expected_color_id": expected_color})

        changes = _changes(event, plan)
        if changes:
            _add(report, "changed_in_google", changes)

        if plan.get("gcal_calendar_id") == LIVE_MARK:
            continue                    # черновик уже убран в прошлую сверку
        start_google, _all_day = _moment(event, "start")
        _add(report, "moved_to_live", {
            "plan_id": plan_id, "gcal_event_id": event.get("id"),
            "svoi_event_id": str(plan_id), "title_google": summary,
            "start_google": _show_moment(start_google),
            "draft_event_id": plan.get("gcal_event_id")})
        if not dry_run and plan.get("gcal_event_id"):
            _clean_draft(conn, draft_service, plan, event)

    report["counts"] = {key: report["totals"][key] for key in CATEGORIES}
    report["counts"]["untyped"] = report["untyped"]["total"]
    if report["totals"].get("cancelled_in_live"):
        report["counts"]["cancelled_in_live"] = report["totals"]["cancelled_in_live"]
    if not dry_run:
        mark_read(conn, last_edit)
    return report


def _clean_draft(conn, draft_service, plan: dict, item: dict) -> None:
    """Удалить **черновик** после переноса человеком. Боевое событие не трогаем."""
    draft_id = google_targets.resolve("calendar.draft")
    draft_event_id = plan.get("gcal_event_id")
    deleted = False
    try:
        draft_service.events().delete(calendarId=draft_id, eventId=draft_event_id).execute()
        deleted = True
    except GOOGLE_API_ERRORS:
        # черновика уже нет (человек удалил при переносе) — это ожидаемый исход
        deleted = False
    conn.execute("update event_plan set gcal_event_id = null, gcal_calendar_id = %s "
                 "where id = %s", (LIVE_MARK, plan.get("id")))
    core.audit(conn, "google", None, "event.draft_cleaned", "event_plan", str(plan.get("id")),
               before={"gcal_event_id": draft_event_id},
               after={"draft_deleted": deleted, "live_event_id": item.get("id"),
                      "calendar": "read"})


def freshness(conn) -> list[dict]:
    """Свежесть источников: `as_of`, `last_edit_time`, `stale`, `error` (186 §7, 190 §10.4).

    Значения берутся из `sheet_binding` (последнее чтение/правка файла) и из отметок
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
            last_edit = read_last_edit(conn)
            entry["as_of"] = as_of.isoformat() if as_of else None
            entry["last_edit_time"] = last_edit.isoformat() if last_edit else None
            entry["stale"] = (as_of is None
                              or now - as_of > timedelta(minutes=STALE_AFTER_MINUTES))
        elif key == "drive.root":
            as_of = _parse_setting(conn, DRIVE_READ_KEY)
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

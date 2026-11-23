"""Mini App: карточка события, список окна и настройка типов и цветов.

Зачем отдельный модуль (docs/development/190 §3–§6). До этой карты событие
открывалось только по строке `event_plan` (`GET /tools/events/{id}`), поэтому 446
людских событий календаря не открывались вовсе; список читался одной страницей
«вперёд от сейчас»; типы и цвета правились только кодом.

Что здесь:

* `GET /webapp/events/card` — одна карточка для события любого происхождения:
  ключ — календарь и id события Google, а не `event_plan.id`;
* `GET /webapp/events/list` — окно календаря с листанием по `nextPageToken`;
* `GET|POST /webapp/events/types` — словарь типов, его правка и перекраска.

Правила, которые держит модуль:

1. Контракты чата (`/tools/events/{id}`, `/tools/events/move`,
   `/tools/calendar/events`) не меняются: это другие адреса.
2. Пустое поле отдаётся `null`, а не пустой строкой: экран по этому правилу
   не рисует строку (190 §3.2).
3. `editable` — результат `events._can_move`, той же функции, что проверяет
   перенос: карточка не покажет кнопку, которая получит 403 (§3.3).
4. Читает `schedule.read.all`, настраивает типы `calendar.write`; отказ — словами.
5. Перекраска пишет только в календарь своего режима и только события изменённых
   типов; `apply: "new"` не делает ни одного пишущего вызова (§6.5).

Тесты: `tests/integration/test_event_card_api.py`,
`tests/integration/test_events_list_window.py`,
`tests/webapp/test_miniapp_event_card.py`, `test_miniapp_events_reconcile.py`,
`test_miniapp_event_types.py`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import calendar_sync, core, event_types, google_targets
from ..calendar_blocks import PROP_EVENT_ID, PROP_KIND
from ..errors import ApiError
from ..external import GOOGLE_API_ERRORS
from . import events as events_api

router = APIRouter()

#: Поля события, которые нужны карточке и списку: без них не показать ни метку,
#: ни место, ни описание, а лишние тянут за собой вложения и участников.
EVENT_FIELDS = ("id,summary,description,location,colorId,status,start,end,"
                "extendedProperties,htmlLink")

#: Окно списка: 92 дня — примерно квартал. Больше не даём: это уже не «месяц»,
#: а выгрузка календаря за сезон (190 §4).
MAX_WINDOW_DAYS = 92
MAX_LIMIT = 100
DEFAULT_LIMIT = 50

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CALENDARS = {"draft": "calendar.draft", "read": "calendar.read"}


# --- общее -----------------------------------------------------------------


def _calendar_id(conn, calendar: str) -> str:
    """Идентификатор календаря по имени: черновой — объект записи, боевой — чтения.

    Пустой id — `calendar_id_missing` (503), а не пустая строка в запросе к Google:
    иначе вместо понятной ошибки пришёл бы отказ API без объяснения.
    """
    try:
        value = (google_targets.resolve("calendar.draft") if calendar == "draft"
                 else google_targets.read_id("calendar.read"))
    except ApiError as exc:
        raise ApiError(503, "Календарь не настроен: администратору нужно указать id "
                            "календаря.", code="calendar_id_missing") from exc
    if not str(value or "").strip():
        raise ApiError(503, "Календарь не настроен: администратору нужно указать id "
                            "календаря.", code="calendar_id_missing")
    return str(value).strip()


def _calendar_name(calendar: str | None) -> str:
    if calendar not in CALENDARS:
        raise ApiError(400, "bad_calendar: календарь — draft или read", code="bad_calendar")
    return calendar


def _google_error(exc: Exception, *, event: bool = False) -> ApiError:
    """Отказ Google словами: нет доступа — 403, недоступен — 502, не найдено — 404."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if event and status == 404:
        return ApiError(404, "event_not_found: события нет в этом календаре",
                        code="event_not_found")
    if status in (401, 403):
        return ApiError(403, "Нет доступа к календарю. Проверьте права бота.",
                        code="no_access")
    return ApiError(502, "Календарь недоступен — повторите позже.",
                    code="calendar_unavailable")


def _to_dt(value) -> tuple[datetime | None, bool]:
    """Момент события и признак «на весь день»: `dateTime` или `date`.

    Сначала проверяется форма даты без времени: `datetime.fromisoformat` разбирает
    `2026-11-25` как полночь, и событие «на весь день» получило бы время 00:00.
    """
    text = str(value or "").strip()
    if not text:
        return None, False
    if DAY_RE.match(text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None, False
        return datetime.combine(day, time.min, core.TZ), True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed, False


def _private(event: dict) -> dict:
    return (event.get("extendedProperties") or {}).get("private") or {}


def _color_id(event: dict) -> int | None:
    raw = str(event.get("colorId") or "").strip()
    return int(raw) if raw.isdigit() else None


def _clean(value) -> str | None:
    """Пустая строка — это «поля нет»: экран не рисует подпись без значения."""
    text = str(value if value is not None else "").strip()
    return text or None


def _plan_of(conn, event: dict) -> dict | None:
    """Строка `event_plan` по маркеру `svoi_event_id` (186 §5.1)."""
    raw = str(_private(event).get(PROP_EVENT_ID) or "").strip()
    if not raw.isdigit():
        return None
    return conn.execute(
        "select id, title, kind, status, starts_at, ends_at, location, created_by, "
        "gcal_event_id from event_plan where id = %s", (int(raw),)).fetchone()


def _resolve_kind(conn, private: dict, summary: str | None) -> dict:
    """Тип по правилу I-3: маркер `svoi_kind`, затем метка `[X]` в заголовке."""
    return event_types.resolve(conn, str(private.get(PROP_KIND) or "").strip().lower() or None,
                               calendar_sync.event_label(summary))


# --- карточка ---------------------------------------------------------------


@router.get("/webapp/events/card")
def webapp_event_card(calendar: str | None = None, event_id: str | None = None,
                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Карточка события любого происхождения (190 §3).

    Право `schedule.read.all` — то же, что у списка календаря: карточка показывает
    расписание, а не личные данные. `editable` решает `events._can_move`.
    С V-132 право есть и у роли `troupe`: актёр видит расписание студии, а запись
    (`editable`) по-прежнему решает `schedule.write`.
    """
    viewer = core._webapp_require(who, "schedule.read.all",
                                  deny_text="Календарь закрыт: нет права на расписание.")
    calendar = _calendar_name(calendar)
    if not str(event_id or "").strip():
        raise ApiError(400, "event_id_required: нужен id события Google",
                       code="event_id_required")
    event_id = str(event_id).strip()

    with core.pool.connection() as conn:
        calendar_id = _calendar_id(conn, calendar)
        try:
            event = core._calendar().events().get(calendarId=calendar_id,
                                                  eventId=event_id,
                                                  fields=EVENT_FIELDS).execute() or {}
        except GOOGLE_API_ERRORS as exc:
            raise _google_error(exc, event=True) from None

        plan = _plan_of(conn, event)
        private = _private(event)
        summary = event.get("summary")
        kind = _resolve_kind(conn, private, summary)
        titles = event_types.titles(conn)

        venue_name = None
        creator = None
        if plan is not None:
            if plan["location"]:
                row = conn.execute("select name from venue where code = %s",
                                   (plan["location"],)).fetchone()
                venue_name = row["name"] if row else plan["location"]
            if plan["created_by"]:
                row = conn.execute("select id, display_name from participant where id = %s",
                                   (plan["created_by"],)).fetchone()
                if row is not None:
                    creator = {"id": row["id"], "name": row["display_name"]}
        plan_status = plan["status"] if plan is not None else None
        plan_created_by = plan["created_by"] if plan is not None else None

    start, all_day = _to_dt((event.get("start") or {}).get("dateTime")
                            or (event.get("start") or {}).get("date"))
    end, _end_all_day = _to_dt((event.get("end") or {}).get("dateTime")
                               or (event.get("end") or {}).get("date"))
    start_local = start.astimezone(core.TZ) if start else None
    end_local = end.astimezone(core.TZ) if end else None

    cancelled = str(event.get("status") or "") == "cancelled"
    has_marker = str(private.get(PROP_EVENT_ID) or "").strip().isdigit()
    editable, reason = _editable(has_marker, plan_status, plan_created_by, calendar,
                                 cancelled, viewer)

    return {"ok": True, "event": {
        "calendar": calendar,
        "event_id": str(event.get("id") or event_id),
        "plan_id": plan["id"] if plan is not None else None,
        "letter": kind["letter"], "kind": kind["kind"], "kind_title": kind["kind_title"],
        "title": calendar_sync.plain_title(summary, titles),
        "title_raw": str(summary or ""),
        "color_id": _color_id(event),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "all_day": all_day,
        "date": start_local.date().isoformat() if start_local else None,
        "weekday": core.WEEKDAY_TITLES[start_local.weekday()] if start_local else None,
        "time_from": (None if all_day or not start_local
                      else start_local.strftime("%H:%M")),
        "time_to": (None if all_day or not end_local else end_local.strftime("%H:%M")),
        "location": _clean(event.get("location")) or venue_name,
        "creator": creator,
        "description": _clean(event.get("description")),
        "state": "cancelled" if cancelled else ("draft" if calendar == "draft" else "live"),
        "editable": editable,
        "not_editable_reason": reason,
    }}


def _editable(has_marker: bool, plan_status: str | None, plan_created_by: int | None,
              calendar: str, cancelled: bool, viewer: dict) -> tuple[bool, str | None]:
    """Правило §3.3 в одном месте: первая подходящая причина отказа и есть ответ."""
    if cancelled:
        return False, "cancelled"
    if not has_marker:
        # людское событие: причина одна и та же в любом календаре — его создали в Google
        # (AC-5), поэтому проверка стоит до «боевой календарь».
        return False, "human_event"
    if calendar == "read":
        return False, "live_calendar"
    if plan_status is None or plan_created_by is None:
        return False, "plan_missing"       # маркер бота есть, строки в расписании нет
    if not events_api._can_move({"created_by": plan_created_by}, viewer):
        return False, "not_your_event"
    return True, None


# --- список окна ------------------------------------------------------------


@router.get("/webapp/events/list")
def webapp_events_list(calendar: str | None = None, from_: str | None = Query(default=None, alias="from"),
                       to: str | None = None, page_token: str | None = None,
                       limit: int = DEFAULT_LIMIT,
                       who: dict = Depends(core.webapp_actor)) -> dict:
    """События окна с листанием по `nextPageToken` (190 §4).

    Контракт чата (`/tools/calendar/events`) не меняется: у Mini App свой адрес,
    потому что ему нужно и окно назад, и страницы, и `plan_id` строки.
    """
    core._webapp_require(who, "schedule.read.all",
                         deny_text="Календарь смотрит организатор и педагог.")
    calendar = _calendar_name(calendar)
    time_from, time_to = _window(from_, to)
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ApiError(400, f"bad_limit: от 1 до {MAX_LIMIT}", code="bad_limit")

    with core.pool.connection() as conn:
        calendar_id = _calendar_id(conn, calendar)
        params: dict = {"calendarId": calendar_id,
                        "timeMin": time_from.isoformat(), "timeMax": time_to.isoformat(),
                        "singleEvents": True, "orderBy": "startTime",
                        "maxResults": int(limit), "fields": f"nextPageToken,items({EVENT_FIELDS})"}
        if page_token:
            params["pageToken"] = page_token
        try:
            answer = core._calendar().events().list(**params).execute() or {}
        except GOOGLE_API_ERRORS as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 400 and page_token:
                raise ApiError(400, "bad_page_token: страница не найдена",
                               code="bad_page_token") from None
            raise _google_error(exc) from None

        events = answer.get("items") or []
        markers = [str(_private(event).get(PROP_EVENT_ID) or "").strip() for event in events]
        plan_ids = [int(mark) for mark in markers if mark.isdigit()]
        plans: dict[int, dict] = {}
        if plan_ids:
            rows = conn.execute("select id, title, kind from event_plan where id = any(%s)",
                                (plan_ids,)).fetchall()
            plans = {int(row["id"]): dict(row) for row in rows}
        titles = event_types.titles(conn)

        items = []
        for event, marker in zip(events, markers, strict=True):
            plan_id = int(marker) if marker.isdigit() else None
            kind = _resolve_kind(conn, _private(event), event.get("summary"))
            start, all_day = _to_dt((event.get("start") or {}).get("dateTime")
                                    or (event.get("start") or {}).get("date"))
            end, _ = _to_dt((event.get("end") or {}).get("dateTime")
                            or (event.get("end") or {}).get("date"))
            items.append({
                "event_id": str(event.get("id") or ""),
                "title": calendar_sync.plain_title(event.get("summary"), titles),
                "title_raw": str(event.get("summary") or ""),
                "letter": kind["letter"], "kind": kind["kind"],
                "color_id": _color_id(event),
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "all_day": all_day,
                "location": _clean(event.get("location")),
                "status": str(event.get("status") or ""),
                "has_plan": plan_id is not None and plan_id in plans,
                "plan_id": plan_id if plan_id in plans else None,
            })

    return {"ok": True, "calendar": calendar,
            "from": time_from.date().isoformat(),
            "to": (time_to - timedelta(seconds=1)).date().isoformat(),
            "count": len(items), "total_scanned": len(items),
            "next_page_token": answer.get("nextPageToken") or None,
            "items": items}


def _window(from_: str | None, to: str | None) -> tuple[datetime, datetime]:
    """Окно чтения: обязательные даты, `to >= from`, не больше 92 дней."""
    if not from_ or not to or not DAY_RE.match(str(from_)) or not DAY_RE.match(str(to)):
        raise ApiError(400, "bad_window: нужны даты from и to в виде ГГГГ-ММ-ДД",
                       code="bad_window")
    try:
        start_day = date.fromisoformat(from_)
        end_day = date.fromisoformat(to)
    except ValueError:
        raise ApiError(400, "bad_window: дата не разбирается", code="bad_window") from None
    if end_day < start_day:
        raise ApiError(400, "bad_window: конец окна раньше начала", code="bad_window")
    # верхняя граница — исключающая: последний день окна входит целиком
    time_from = datetime.combine(start_day, time.min, core.TZ)
    time_to = datetime.combine(end_day + timedelta(days=1), time.min, core.TZ)
    if (time_to - time_from).days > MAX_WINDOW_DAYS:
        raise ApiError(400, f"bad_window: окно больше {MAX_WINDOW_DAYS} дней",
                       code="bad_window")
    return time_from, time_to


# --- типы и цвета -----------------------------------------------------------


class TypeIn(BaseModel):
    """Один тип в списке настройки: код `null` — новый тип (190 §6.3)."""

    code: str | None = Field(default=None, max_length=32)
    letter: str | None = Field(default=None, max_length=2)
    title: str = Field(default="", max_length=200)
    color_id: int | None = Field(default=None, ge=0, le=99)
    sort_order: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True


class TypesIn(BaseModel):
    """Тело `POST /webapp/events/types`: режим перекраски и весь список типов."""

    apply: str | None = None
    types: list[TypeIn] = Field(default_factory=list)


@router.get("/webapp/events/types")
def webapp_event_types(who: dict = Depends(core.webapp_actor)) -> dict:
    """Словарь типов и палитра (190 §6.2). Право — `calendar.write`."""
    core._webapp_require(who, "calendar.write",
                         deny_text="Типы и цвета настраивает организатор.")
    with core.pool.connection() as conn:
        items = event_types.types(conn)
        mode = google_targets.google_mode(conn)
    return {"ok": True, "can_edit": True, "mode": mode,
            "palette": [dict(item) for item in event_types.CALENDAR_COLORS],
            "types": items}


@router.post("/webapp/events/types")
def webapp_event_types_save(body: TypesIn,
                            who: dict = Depends(core.webapp_actor)) -> dict:
    """Сохранить настройку и, если выбрано «Перекрасить все», перекрасить события.

    Проверяется весь список до записи: любая ошибка — `field`/`code` элемента,
    ничего не сохранено (§6.3). Перекраска идёт после сохранения: настройка уже
    применена, даже если Google отказал на середине (§6.4).
    """
    viewer = core._webapp_require(who, "calendar.write",
                                  deny_text="Типы и цвета настраивает организатор.")
    payload = [item.model_dump() for item in body.types]

    with core.pool.connection() as conn:
        problems = event_types.validate(conn, payload, apply_mode=body.apply)
        if problems:
            first = problems[0]
            raise ApiError(first["status"], f"{first['code']}: {first['detail']}",
                           code=first["code"])
        # Проверка режима — до записи (AC-15, I-7): в целевом режиме боевой календарь
        # писать нельзя, поэтому отказ обязан оставить настройку нетронутой.
        if (body.apply == "all"
                and google_targets.google_mode(conn) == google_targets.MODE_TARGET):
            raise ApiError(
                409,
                "recolor_target_forbidden: перекраска в боевом календаре не включена; "
                "сохраните настройку с «Только новые»",
                code="recolor_target_forbidden")
        saved = event_types.save(conn, payload, actor_id=viewer.get("id"),
                                 apply_mode=str(body.apply))

    recolored = {"recolored": 0, "total": 0, "failed": [], "failed_more": 0}
    if body.apply == "all" and saved["changed"]:
        with core.pool.connection() as conn:
            recolored = event_types.recolor(conn, saved["changed"],
                                            apply_mode=str(body.apply),
                                            actor_id=viewer.get("id"))
    return {"ok": True, "saved": True, **recolored}

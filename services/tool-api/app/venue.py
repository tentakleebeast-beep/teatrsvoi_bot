"""
Занятость зала по данным Google Calendar freebusy (docs/architecture/46).

Календарь знает, когда зал занят (аренда, показы, репетиции). Для аренды это
единственное, что важно: люди могут быть свободны, но зал занят — значит сдать нельзя,
и наоборот. Область субаренды — вторая половина действия С6; сетка часов у неё
та же, что у людей (docs/architecture/40 §2), иначе один час давал бы два ответа.

Модуль чистый: на вход ответ freebusy, на выход — свободные окна зала.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .rules import WORK_DAY_END, WORK_DAY_START

DAY_START = WORK_DAY_START
DAY_END = WORK_DAY_END          # граница «до», не включается: последняя клетка 23:00–24:00


@dataclass(frozen=True)
class Busy:
    starts_at: datetime
    ends_at: datetime
    title: str = ""


@dataclass(frozen=True)
class VenueWindow:
    day: date
    from_hour: int
    to_hour: int

    @property
    def hours(self) -> int:
        return self.to_hour - self.from_hour

    def as_dict(self) -> dict:
        return {"day": self.day.isoformat(), "from_hour": self.from_hour,
                "to_hour": self.to_hour, "hours": self.hours}


def _dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_freebusy(payload: dict, calendar_id: str) -> list[Busy]:
    """
    Ответ freebusy → список занятых интервалов.

    Google возвращает {"calendars": {"<id>": {"busy": [...]}}}; ошибки по календарю
    приходят в поле "errors" — в этом случае считаем, что данных нет.
    """
    calendars = (payload or {}).get("calendars") or {}
    block = calendars.get(calendar_id) or {}
    if block.get("errors"):
        return []

    result: list[Busy] = []
    for item in block.get("busy") or []:
        start, end = _dt(item.get("start")), _dt(item.get("end"))
        if start and end and end > start:
            result.append(Busy(start, end))
    result.sort(key=lambda b: b.starts_at)
    return result


def busy_for_studio(events: list[dict]) -> list[Busy]:
    """
    Занятость студии целиком — как одного помещения.

    Решение 27.10.2026: пока считаем студию единым пространством. Мероприятие
    в гримёрке занимает студию так же, как мероприятие на сцене: параллельные
    занятия автоматизируем позже. Поэтому здесь учитываются все события
    периода, независимо от `svoi_venue`.
    """
    result: list[Busy] = []
    for raw in events:
        start_raw = (raw.get("start") or {}).get("dateTime")
        end_raw = (raw.get("end") or {}).get("dateTime")
        if not start_raw or not end_raw:
            continue
        start, end = _dt(start_raw), _dt(end_raw)
        if start and end and end > start:
            result.append(Busy(start, end, str(raw.get("summary") or "")))
    result.sort(key=lambda b: b.starts_at)
    return result


def busy_for_venue(events: list[dict], venue_code: str,
                   default_venue_code: str = "stage") -> list[Busy]:
    """
    Занятость одного пространства из событий общего календаря.

    Событие без `svoi_venue` считается событием основного пространства (сцена):
    так старые события не потеряются. Репетиция на сцене и разбор роли
    в гримёрке в одно время — два события в разных пространствах, они
    не блокируют друг друга.
    """
    result: list[Busy] = []
    for raw in events:
        private = (raw.get("extendedProperties") or {}).get("private") or {}
        code = (private.get("svoi_venue") or default_venue_code).strip().lower()
        if code != venue_code.strip().lower():
            continue
        start_raw = (raw.get("start") or {}).get("dateTime")
        end_raw = (raw.get("end") or {}).get("dateTime")
        if not start_raw or not end_raw:
            continue                       # событие на весь день зал не занимает по часам
        start, end = _dt(start_raw), _dt(end_raw)
        if start and end and end > start:
            result.append(Busy(start, end, str(raw.get("summary") or "")))
    result.sort(key=lambda b: b.starts_at)
    return result


def merge_busy(busy: list[Busy]) -> list[Busy]:
    """Склеивает пересекающиеся интервалы: 12–14 и 13–15 → 12–15."""
    merged: list[Busy] = []
    for item in sorted(busy, key=lambda b: b.starts_at):
        if merged and item.starts_at <= merged[-1].ends_at:
            last = merged[-1]
            merged[-1] = Busy(last.starts_at, max(last.ends_at, item.ends_at), last.title)
        else:
            merged.append(item)
    return merged


def find_venue_windows(busy: list[Busy],
                       days: list[date],
                       min_hours: int = 2,
                       day_start: int = DAY_START,
                       day_end: int = DAY_END,
                       limit: int = 5) -> list[VenueWindow]:
    """
    Свободные окна зала.

    Работаем по часам: час считается занятым, если пересекается с занятым
    интервалом хотя бы частично. Так же, как в событиях (docs/architecture/45 §3) —
    округление в пользу безопасности. Границы дня — общие с поиском по людям:
    часы day_start…day_end-1, последняя клетка 23:00–24:00 (docs/architecture/40 §2).
    """
    day_start, day_end = max(0, int(day_start)), min(24, int(day_end))
    if int(min_hours) < 1 or day_start >= day_end:
        return []

    merged = merge_busy(busy)
    windows: list[VenueWindow] = []

    for day in days:
        busy_hours = set()
        for item in merged:
            moment = item.starts_at.replace(minute=0, second=0, microsecond=0)
            while moment < item.ends_at:
                if moment.date() == day and day_start <= moment.hour < day_end:
                    busy_hours.add(moment.hour)
                moment += timedelta(hours=1)

        run_start: int | None = None
        for hour in range(day_start, day_end + 1):
            free = day_start <= hour < day_end and hour not in busy_hours
            if free and run_start is None:
                run_start = hour
            elif not free and run_start is not None:
                if hour - run_start >= min_hours:
                    windows.append(VenueWindow(day, run_start, hour))
                run_start = None

    windows.sort(key=lambda w: (-w.hours, w.day.toordinal(), w.from_hour))
    return windows[:limit]


def intersect(venue: list[VenueWindow],
              people: list[tuple[date, int, int]]) -> list[VenueWindow]:
    """
    Пересечение свободного зала с окнами людей.

    people — список (день, с какого часа, до какого часа), который уже посчитан
    политикой окон. Возвращает только те окна, где свободно и то и другое.
    """
    result: list[VenueWindow] = []
    for window in venue:
        for day, from_hour, to_hour in people:
            if day != window.day:
                continue
            start = max(window.from_hour, from_hour)
            end = min(window.to_hour, to_hour)
            if end - start >= 1:
                result.append(VenueWindow(day, start, end))
    return result

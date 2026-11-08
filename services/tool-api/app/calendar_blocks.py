"""
События календаря как занятость (docs/architecture/45).

Календарь знает «что и когда», наша база — «кто и как». Мост между ними:
`extendedProperties.private` в событии. Если событие создано ботом, в нём есть:

    svoi_kind            — rehearsal | show | training | rental | meeting
    svoi_project_id      — спектакль, к которому относится
    svoi_participant_ids — "12,16,30" — кого событие занимает

Дальше эти часы вычитаются из свободного времени людей при поиске окон.
Событие без `svoi_participant_ids` никого не блокирует: арендатор чужого
мероприятия наших людей не занимает.

Модуль чистый: на вход словари событий Google, на выход — занятые часы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAY_START = 8
DAY_END = 23
PROP_KIND = "svoi_kind"
PROP_PROJECT = "svoi_project_id"
PROP_PEOPLE = "svoi_participant_ids"
PROP_SOURCE = "source"


@dataclass(frozen=True)
class ParsedEvent:
    id: str
    title: str
    kind: str
    project_id: int | None
    participant_ids: tuple[int, ...]
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool = False

    @property
    def blocks_people(self) -> bool:
        return bool(self.participant_ids)


def _split_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    ids = []
    for chunk in str(raw).replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return tuple(sorted(set(ids)))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:                                    # только дата
            return datetime.fromisoformat(text + "T00:00:00")
        except ValueError:
            return None


def parse_event(event: dict) -> ParsedEvent:
    """Событие Google → структура, с которой можно работать."""
    private = (event.get("extendedProperties") or {}).get("private") or {}
    start_raw = (event.get("start") or {}).get("dateTime")
    end_raw = (event.get("end") or {}).get("dateTime")
    all_day = start_raw is None

    return ParsedEvent(
        id=str(event.get("id") or ""),
        title=str(event.get("summary") or ""),
        kind=str(private.get(PROP_KIND) or "").strip().lower(),
        project_id=int(private[PROP_PROJECT]) if str(private.get(PROP_PROJECT, "")).isdigit() else None,
        participant_ids=_split_ids(private.get(PROP_PEOPLE)),
        starts_at=_parse_dt(start_raw or (event.get("start") or {}).get("date")),
        ends_at=_parse_dt(end_raw or (event.get("end") or {}).get("date")),
        all_day=all_day,
    )


def event_hours(event: ParsedEvent,
                day_start: int = DAY_START,
                day_end: int = DAY_END) -> set[tuple[date, int]]:
    """
    Часы, которые событие занимает.

    Событие 19:00–21:30 занимает часы 19, 20 и 21: в 21:00 человек ещё занят,
    а начинать что-то новое внутри занятого часа нельзя. Округляем в пользу
    безопасности — лучше не предложить окно, чем предложить невозможное.
    """
    if event.starts_at is None or event.ends_at is None:
        return set()

    if event.all_day:
        start_day = event.starts_at.date()
        last_day = (event.ends_at - timedelta(days=1)).date() if event.ends_at else start_day
        days = []
        current = start_day
        while current <= last_day:
            days.append(current)
            current += timedelta(days=1)
        return {(day, hour) for day in days for hour in range(day_start, day_end + 1)}

    hours: set[tuple[date, int]] = set()
    moment = event.starts_at.replace(minute=0, second=0, microsecond=0)
    while moment < event.ends_at:
        if day_start <= moment.hour <= day_end:
            hours.add((moment.date(), moment.hour))
        moment += timedelta(hours=1)
    return hours


def blocked_hours(events: list[dict],
                  day_start: int = DAY_START,
                  day_end: int = DAY_END,
                  only_days: set[date] | None = None) -> dict[int, set[tuple[date, int]]]:
    """
    Кто и когда занят по событиям.

    Возвращает {participant_id: {(дата, час), ...}} — готово к вычитанию.
    """
    result: dict[int, set[tuple[date, int]]] = {}
    for raw in events:
        event = parse_event(raw)
        if not event.blocks_people:
            continue
        hours = event_hours(event, day_start, day_end)
        if only_days:
            hours = {item for item in hours if item[0] in only_days}
        if not hours:
            continue
        for pid in event.participant_ids:
            result.setdefault(pid, set()).update(hours)
    return result


def apply_blocks(marks: dict[int, dict[tuple[date, int], bool] | None],
                 blocks: dict[int, set[tuple[date, int]]],
                 day_start: int = DAY_START,
                 day_end: int = DAY_END) -> tuple[dict[int, dict[tuple[date, int], bool] | None], int]:
    """
    Вычитает события из отметок занятости.

    Не создаёт новых людей: если у человека нет ни одной отметки, но есть событие —
    он всё равно занят в эти часы, поэтому строка появляется.
    """
    applied = 0
    result: dict[int, dict[tuple[date, int], bool] | None] = {k: (dict(v) if v else v)
                                                              for k, v in marks.items()}
    for pid, hours in blocks.items():
        table = result.get(pid)
        if table is None:
            table = {}
        for key in hours:
            if table.get(key) is not False:
                applied += 1
            table[key] = False
        result[pid] = table
    return result, applied

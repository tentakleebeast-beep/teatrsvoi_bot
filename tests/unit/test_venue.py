"""Свободные окна зала по freebusy (docs/architecture/46)."""

from datetime import date, datetime, timedelta, timezone

from app import slots as slots_module
from app import venue as venue_module
from app.venue import Busy, VenueWindow, find_venue_windows, intersect, merge_busy, parse_freebusy

TZ = timezone(timedelta(hours=4))
MON = date(2026, 12, 7)


def busy(start: str, end: str) -> Busy:
    return Busy(datetime.fromisoformat(start), datetime.fromisoformat(end))


def test_разбор_ответа_freebusy():
    payload = {"calendars": {"venue": {"busy": [
        {"start": "2026-12-07T19:00:00+04:00", "end": "2026-12-07T22:00:00+04:00"}]}}}
    parsed = parse_freebusy(payload, "venue")
    assert len(parsed) == 1 and parsed[0].starts_at.hour == 19


def test_ошибка_календаря_значит_нет_данных():
    payload = {"calendars": {"venue": {"errors": [{"reason": "notFound"}], "busy": []}}}
    assert parse_freebusy(payload, "venue") == []
    assert parse_freebusy({}, "venue") == []


def test_склейка_пересечений():
    merged = merge_busy([busy("2026-12-07T12:00:00+04:00", "2026-12-07T14:00:00+04:00"),
                         busy("2026-12-07T13:00:00+04:00", "2026-12-07T15:00:00+04:00")])
    assert len(merged) == 1
    assert merged[0].ends_at.hour == 15


def test_окна_между_занятостью():
    windows = find_venue_windows(
        [busy("2026-12-07T12:00:00+04:00", "2026-12-07T14:00:00+04:00")], [MON], min_hours=2)
    spans = [(w.from_hour, w.to_hour) for w in windows]
    assert (8, 12) in spans and (14, 24) in spans, 'зал свободен до конца дня'


def test_минимальная_длина_соблюдается():
    windows = find_venue_windows(
        [busy("2026-12-07T10:00:00+04:00", "2026-12-07T14:00:00+04:00")], [MON], min_hours=3)
    assert [(w.from_hour, w.to_hour) for w in windows] == [(14, 24)]


def test_зал_занят_весь_день():
    windows = find_venue_windows(
        [busy("2026-12-07T08:00:00+04:00", "2026-12-07T23:00:00+04:00")], [MON], min_hours=2)
    assert windows == []


def test_частичный_час_считается_занятым():
    windows = find_venue_windows(
        [busy("2026-12-07T19:30:00+04:00", "2026-12-07T20:30:00+04:00")], [MON],
        min_hours=1, limit=10)
    hours = {(w.day, h) for w in windows for h in range(w.from_hour, w.to_hour)}
    assert (MON, 19) not in hours and (MON, 20) not in hours


def test_пересечение_зала_и_людей():
    venue = find_venue_windows(
        [busy("2026-12-07T18:00:00+04:00", "2026-12-07T20:00:00+04:00")], [MON], min_hours=1)
    people = [(MON, 19, 23)]
    result = intersect(venue, people)
    assert result and all(w.from_hour >= 20 for w in result)


def test_пересечение_пустое():
    venue = find_venue_windows(
        [busy("2026-12-07T08:00:00+04:00", "2026-12-07T20:00:00+04:00")], [MON], min_hours=1)
    assert intersect(venue, [(MON, 8, 12)]) == []


def test_ночной_час_зала_режет_окно():
    """Зал и люди живут на одной сетке: последняя клетка 23:00–24:00
    (docs/architecture/40 §2, docs/architecture/46 §3)."""
    windows = find_venue_windows(
        [busy("2026-12-07T23:30:00+04:00", "2026-12-07T23:55:00+04:00")], [MON],
        min_hours=1, limit=10)
    spans = [(w.from_hour, w.to_hour) for w in windows]
    assert spans == [(8, 23)], "аренда в 23:30 занимает последнюю клетку дня"


def test_зал_и_люди_отдают_один_день_одинаково():
    """Границы дня у двух областей совпадают: 08:00–24:00, 16 клеток."""
    assert (venue_module.DAY_START, venue_module.DAY_END) == (
        slots_module.DAY_START, slots_module.DAY_END)
    assert find_venue_windows([], [MON], min_hours=1) == [VenueWindow(MON, 8, 24)]


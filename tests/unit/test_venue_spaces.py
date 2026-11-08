"""Пространства: параллельные мероприятия не мешают друг другу (docs/architecture/47)."""

from datetime import date, datetime, timedelta, timezone

from app.venue import busy_for_studio, busy_for_venue, find_venue_windows

TZ = timezone(timedelta(hours=4))
MON = date(2026, 12, 7)


def ev(summary, start, end, venue=None):
    private = {}
    if venue:
        private["svoi_venue"] = venue
    return {"summary": summary,
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "extendedProperties": {"private": private}}


REHEARSAL = ev("Репетиция - Жизнь насекомых", "2026-12-07T18:00:00+04:00",
               "2026-12-07T21:00:00+04:00", "stage")
DRESSING = ev("Репетиция - Разбор роли", "2026-12-07T18:00:00+04:00",
              "2026-12-07T20:00:00+04:00", "dressing")


def test_сцена_и_гримёрка_не_мешают_друг_другу():
    stage = busy_for_venue([REHEARSAL, DRESSING], "stage")
    dressing = busy_for_venue([REHEARSAL, DRESSING], "dressing")
    assert len(stage) == 1 and len(dressing) == 1
    assert stage[0].starts_at.hour == 18 and dressing[0].starts_at.hour == 18


def test_событие_без_зала_считается_сценой():
    legacy = ev("Старое событие", "2026-12-07T12:00:00+04:00",
                "2026-12-07T14:00:00+04:00")
    assert len(busy_for_venue([legacy], "stage")) == 1
    assert busy_for_venue([legacy], "dressing") == []


def test_свободные_окна_по_пространству():
    # на сцене репетиция вечером, в гримёрке пусто
    stage_windows = find_venue_windows(busy_for_venue([REHEARSAL], "stage"), [MON],
                                       min_hours=2, limit=5)
    dressing_windows = find_venue_windows(busy_for_venue([REHEARSAL], "dressing"), [MON],
                                          min_hours=2, limit=5)
    # сцена занята 18:00–21:00, после репетиции снова свободна
    assert sorted((w.from_hour, w.to_hour) for w in stage_windows) == [(8, 18), (21, 24)]
    assert [(w.from_hour, w.to_hour) for w in dressing_windows] == [(8, 24)]


def test_два_события_на_сцене_склеиваются():
    second = ev("Показ - Вечер", "2026-12-07T20:30:00+04:00", "2026-12-07T23:00:00+04:00",
                "stage")
    busy = busy_for_venue([REHEARSAL, second], "stage")
    windows = find_venue_windows(busy, [MON], min_hours=1, limit=5)
    # репетиция 18–21 и показ 20:30–23 склеиваются в 18–23
    assert sorted((w.from_hour, w.to_hour) for w in windows) == [(8, 18), (23, 24)]


def test_чужое_пространство_не_учитывается():
    busy = busy_for_venue([DRESSING], "backstage")
    assert busy == []


def test_студия_считается_одним_помещением():
    """Пока параллельные занятия не автоматизируем: гримёрка занимает студию."""
    busy = busy_for_studio([REHEARSAL, DRESSING])
    windows = find_venue_windows(busy, [MON], min_hours=2, limit=5)
    # 18–20 занято гримёркой, 18–21 сценой → свободно только до 18 и после 21
    assert sorted((w.from_hour, w.to_hour) for w in windows) == [(8, 18), (21, 24)]

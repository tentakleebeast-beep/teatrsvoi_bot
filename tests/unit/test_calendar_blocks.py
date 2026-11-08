"""События календаря → занятость людей (docs/architecture/45)."""

from datetime import date, datetime, timedelta, timezone

from app.calendar_blocks import apply_blocks, blocked_hours, event_hours, parse_event

TZ = timezone(timedelta(hours=4))
MON = date(2026, 12, 7)


def event(summary="Показ - Жизнь насекомых", start="2026-12-07T19:00:00+04:00",
          end="2026-12-07T22:00:00+04:00", kind="show", project="3",
          people="12,16", extra=None):
    private = {}
    if kind:
        private["svoi_kind"] = kind
    if project:
        private["svoi_project_id"] = project
    if people:
        private["svoi_participant_ids"] = people
    private.update(extra or {})
    return {"id": "ev1", "summary": summary,
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "extendedProperties": {"private": private}}


def test_разбор_события():
    parsed = parse_event(event())
    assert parsed.kind == "show"
    assert parsed.project_id == 3
    assert parsed.participant_ids == (12, 16)
    assert parsed.blocks_people is True


def test_событие_без_участников_никого_не_блокирует():
    parsed = parse_event(event(people=None))
    assert parsed.blocks_people is False
    assert blocked_hours([event(people=None)]) == {}


def test_часы_события_считаются_по_началу():
    hours = event_hours(parse_event(event(start="2026-12-07T19:00:00+04:00",
                                         end="2026-12-07T21:30:00+04:00")))
    # час 21 тоже занят: в 21:00 человек ещё на показе
    assert hours == {(MON, 19), (MON, 20), (MON, 21)}


def test_событие_через_полночь():
    hours = event_hours(parse_event(event(start="2026-12-07T22:00:00+04:00",
                                         end="2026-12-08T01:00:00+04:00")))
    assert (MON, 22) in hours and (MON, 23) in hours
    assert (date(2026, 12, 8), 1) not in hours, "после полуночи рабочие часы не считаем"


def test_весь_день():
    all_day = {"id": "ev2", "summary": "Показ",
               "start": {"date": "2026-12-07"}, "end": {"date": "2026-12-08"},
               "extendedProperties": {"private": {"svoi_participant_ids": "5"}}}
    parsed = parse_event(all_day)
    assert parsed.all_day is True
    hours = event_hours(parsed)
    assert (MON, 8) in hours and (MON, 23) in hours


def test_блокировка_по_людям():
    blocks = blocked_hours([event()])
    assert blocks[12] == {(MON, 19), (MON, 20), (MON, 21)}
    assert blocks[16] == blocks[12]


def test_несколько_событий_складываются():
    blocks = blocked_hours([event(), event(start="2026-12-07T10:00:00+04:00",
                                           end="2026-12-07T12:00:00+04:00")])
    assert (MON, 10) in blocks[12] and (MON, 19) in blocks[12]


def test_применение_блокировки():
    marks = {12: {(MON, 19): True, (MON, 20): True}, 16: None}
    result, applied = apply_blocks(marks, blocked_hours([event()]))
    assert result[12][(MON, 19)] is False
    assert result[12][(MON, 20)] is False
    assert result[16][(MON, 19)] is False, "событие делает человека занятым и без отметок"
    assert applied >= 4


def test_чужие_часы_не_трогаем():
    marks = {12: {(MON, 10): True}}
    result, _ = apply_blocks(marks, blocked_hours([event()]))
    assert result[12][(MON, 10)] is True

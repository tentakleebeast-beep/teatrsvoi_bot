"""Сводка «свободно по дням» в чате (FR-C5, обращение №11)."""

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_день_с_отметками_показывает_часы_и_людей(gw):
    rows = [{"day": "2026-10-28", "free_cnt": 6, "known_cnt": 8,
             "people_cnt": 3, "outside_hours": 0}]

    lines = gw.availability_lines(rows)

    assert lines == ["Свободно по дням (рабочий день 08–23):",
                     "  ср, 28.10: свободно 6 из 8 ч, людей 3"]


def test_день_без_отметок_не_пропадает(gw):
    rows = [{"day": "2026-10-29", "free_cnt": 0, "known_cnt": 0,
             "people_cnt": 0, "outside_hours": 0}]

    assert gw.availability_lines(rows)[1] == "  чт, 29.10: отметок нет"


def test_часы_вне_рабочего_дня_показаны_отдельно(gw):
    rows = [{"day": "2026-10-30", "free_cnt": 2, "known_cnt": 3,
             "people_cnt": 1, "outside_hours": 2}]

    assert gw.availability_lines(rows)[1].endswith("; вне 08–23 ещё 2 ч")


def test_событие_со_ссылкой_ведёт_в_календарь(gw):
    """Обращение №10: ссылка не отдельной строкой, а внутри названия."""
    events = [{"start": "2026-10-29T18:00:00+04:00", "end": "2026-10-29T20:00:00+04:00",
               "title": "Бунин & Ко <репетиция>", "kind": "rehearsal",
               "link": "https://calendar.google.com/event?eid=abc"}]

    lines = gw.schedule_lines(events)

    assert lines[0] == "Расписание на 7 дней:"
    assert lines[2] == "Четверг, 29.10"
    assert lines[3] == ('• 18:00–20:00 Репетиция: <a href="https://calendar.google.com/'
                        'event?eid=abc">Бунин &amp; Ко &lt;репетиция&gt;</a>')
    assert lines[3].count("\n") == 0, "ссылка больше не идёт отдельной строкой"


def test_событие_без_ссылки_остаётся_текстом(gw):
    events = [{"start": "2026-10-30T10:00:00+04:00", "end": "2026-10-30T12:00:00+04:00",
               "title": "Показ", "kind": None, "link": None}]

    assert gw.schedule_lines(events)[3] == "• 10:00–12:00 Показ"
    assert "<a " not in gw.schedule_lines(events)[3]


def test_пустой_день_без_даты_не_ломает_разбор(gw):
    events = [{"start": None, "end": None, "title": "мусор", "kind": None, "link": None}]

    assert gw.schedule_lines(events) == ["Расписание на 7 дней:"]


def test_html_join_не_рвёт_тег(gw):
    long_title = "x" * 100
    lines = [f'<a href="https://e/{i}">{long_title}</a>' for i in range(40)]

    text = gw.html_join(lines, limit=250)

    assert len(text) <= 250
    assert text.count("<a ") == text.count("</a>")

"""Раскрашенная сетка занятости: цвет → слоты (docs/development/22, docs/development/16 §4.4)."""

from datetime import date

from app.schedule_grid import (
    build_slots,
    classify_color,
    merge_intervals,
    parse_day,
    parse_hour_header,
    summary,
)


def test_красный_это_занят():
    assert classify_color("#EA4335") == "busy"
    assert classify_color({"red": 0.92, "green": 0.26, "blue": 0.21}) == "busy"


def test_зелёный_это_свободен():
    assert classify_color("#34A853") == "free"
    assert classify_color({"red": 0.06, "green": 0.65, "blue": 0.32}) == "free"


def test_жёлтый_это_не_уверен():
    assert classify_color("#FBBC04") == "uncertain"


def test_белая_ячейка_это_не_отмечено():
    assert classify_color({"red": 1, "green": 1, "blue": 1}) is None
    assert classify_color(None) is None
    assert classify_color("#FFFEF7") is None


def test_неизвестный_цвет_не_угадывается():
    assert classify_color("#7B1FA2") is None        # фиолетовый — не наш цвет


def test_шапка_часов():
    assert parse_hour_header(["08:00", "9", "10:00", "Итог", ""]) == [8, 9, 10, None, None]


def test_разбор_дат_с_годом_и_без():
    assert parse_day("01.11.2026") == date(2026, 11, 1)
    assert parse_day("01.11", default_month="2026-11") == date(2026, 11, 1)
    assert parse_day("2026-11-01") == date(2026, 11, 1)
    assert parse_day("не дата") is None


def test_сетка_даёт_слоты_и_пропускает_пустое():
    header = ["Дата", "08:00", "09:00", "10:00"]
    rows = [
        ["01.11.2026", {"color": "#EA4335"}, {"color": "#FFFFFF"}, {"color": "#34A853"}],
        ["02.11.2026", {"color": "#FBBC04"}, None, None],
    ]
    slots = build_slots(header, rows, default_month="2026-11")
    got = {(s.day.isoformat(), s.hour): s.status for s in slots}
    assert got == {
        ("2026-11-01", 8): "busy",
        ("2026-11-01", 10): "free",
        ("2026-11-02", 8): "uncertain",
    }


def test_старый_формат_ноль_единица_поддерживается():
    header = ["Дата", "08:00", "09:00"]
    rows = [["01.11.2026", "1", "0"]]
    slots = build_slots(header, rows, default_month="2026-11")
    assert {(s.hour, s.status) for s in slots} == {(8, "free"), (9, "busy")}


def test_интервалы_склеиваются():
    header = ["Дата", "18:00", "19:00", "20:00", "21:00"]
    rows = [["01.11.2026", {"color": "#EA4335"}, {"color": "#EA4335"},
             {"color": "#FFFFFF"}, {"color": "#EA4335"}]]
    slots = build_slots(header, rows, default_month="2026-11")
    intervals = merge_intervals(slots)
    assert intervals[0] == {"day": "2026-11-01", "from": 18, "to": 20,
                            "status": "busy", "title": "занят"}
    assert intervals[1]["from"] == 21


def test_вертикальная_сетка_даты_в_столбцах():
    from app.schedule_grid import parse_vertical_grid

    dates_row = [{"value": "Час"}, {"value": "01.11.2026"}, {"value": "02.11.2026"}]
    hour_rows = [
        [{"value": "8:00"}, {"color": {"red": 0.92, "green": 0.26, "blue": 0.21}},
         {"color": {"red": 0.20, "green": 0.66, "blue": 0.33}}],
        [{"value": "9:00"}, {"color": {"red": 1, "green": 1, "blue": 1}}, None],
    ]
    slots = parse_vertical_grid(dates_row, hour_rows)
    got = {(s.day.isoformat(), s.hour): s.status for s in slots}
    assert got == {("2026-11-01", 8): "busy", ("2026-11-02", 8): "free"}


def test_текстовые_статусы():
    from app.schedule_grid import hours_from_text

    assert hours_from_text("занята до 19:00")[10] == "busy"
    assert hours_from_text("занята до 19:00")[20] == "free"
    assert hours_from_text("свободно с 19:00")[10] == "busy"
    assert hours_from_text("свободно с 19:00")[20] == "free"
    assert set(hours_from_text("пока не понятно").values()) == {"uncertain"}
    assert hours_from_text("") == {}


def test_сводка_по_клеткам():
    header = ["Дата", "08:00", "09:00"]
    rows = [["01.11.2026", {"color": "#EA4335"}, {"color": "#34A853"}]]
    s = summary(build_slots(header, rows, default_month="2026-11"))
    assert s["cells"] == 2 and s["days"] == 1
    assert s["busy"] == 1 and s["free"] == 1 and s["uncertain"] == 0


# --- человеческие формулировки из боевого файла (docs/development/22) --------------------

def test_словесные_часы():
    from app.schedule_grid import hours_from_text
    assert hours_from_text("свободен после семи")[19] == "free"
    assert hours_from_text("свободен после семи")[18] == "busy"
    assert hours_from_text("занята до восьми")[20] == "free"
    assert hours_from_text("занята до восьми")[19] == "busy"


def test_диапазон_с_до():
    from app.schedule_grid import hours_from_text, parse_range
    assert parse_range("занята с 14 до 22") == (14, 22)
    assert parse_range("с 10:00 по 18:00") == (10, 18)
    assert parse_range("свободен") is None

    marks = hours_from_text("занята с 14 до 22")
    assert marks[13] == "free" and marks[14] == "busy"
    assert marks[21] == "busy" and marks[22] == "free"


def test_не_будет_в_самаре():
    from app.schedule_grid import hours_from_text
    marks = hours_from_text("занята (не будет в Самаре)")
    assert marks[12] == "busy"


def test_пока_не_понятно_это_неуверенность():
    from app.schedule_grid import hours_from_text
    marks = hours_from_text("пока не понятно")
    assert marks[12] == "uncertain"

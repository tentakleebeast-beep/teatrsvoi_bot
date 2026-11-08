"""
Разбор «раскрашенной» сетки занятости (docs/development/22).

Чистые функции: цвет → статус, шапка → часы, строка → дата, сетка → слоты.
Чтение Google-таблицы — в scripts/parse_colored_schedule.py, чтобы тесты
не зависели от сети.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# палитры Google Sheets (и близкие оттенки, которыми люди красят руками)
PALETTE: dict[str, list[str]] = {
    "busy": ["#ea4335", "#f44336", "#e53935", "#d32f2f", "#ff0000", "#cc0000"],
    "free": ["#34a853", "#0f9d58", "#43a047", "#4caf50", "#00b050", "#7bd148"],
    "uncertain": ["#fbbc04", "#ffeb3b", "#ffd966", "#fdd835", "#ffff00", "#ffe599"],
}

STATUS_TITLES = {"busy": "занят", "free": "свободен", "uncertain": "не уверен"}
COLOR_DISTANCE = 0.16          # допуск: «похожий оттенок» всё равно считается цветом


def _rgb(hex_value: str) -> tuple[float, float, float]:
    h = hex_value.strip().lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def classify_color(bg: dict | str | None) -> str | None:
    """
    Цвет ячейки → статус.
    bg — либо {'red':0.9,'green':0.2,'blue':0.2}, либо '#ea4335'.
    Белый, пустой и «почти белый» дают None (значит «не отмечено»).
    """
    if bg is None:
        return None
    if isinstance(bg, str):
        rgb = _rgb(bg)
    else:
        rgb = (float(bg.get("red", 1)), float(bg.get("green", 1)), float(bg.get("blue", 1)))

    if min(rgb) > 0.93:                     # белый лист
        return None

    best, best_dist = None, 9.9
    for status, colors in PALETTE.items():
        for hex_color in colors:
            ref = _rgb(hex_color)
            dist = sum((a - b) ** 2 for a, b in zip(rgb, ref, strict=False)) ** 0.5
            if dist < best_dist:
                best, best_dist = status, dist
    return best if best_dist <= COLOR_DISTANCE else None


HOUR_RE = re.compile(r"^(\d{1,2})(?::00)?$")
DATE_RE = re.compile(r"^(\d{1,2})[.\s/-](\d{1,2})(?:[.\s/-](\d{2,4}))?$")


def parse_hour_header(values: list) -> list[int | None]:
    """Шапка строки: '08:00', '9', '22:00' → часы; всё остальное → None."""
    hours: list[int | None] = []
    for v in values:
        if v is None or v == "":
            hours.append(None)
            continue
        text = str(v).strip().replace(".", ":")
        m = HOUR_RE.match(text)
        hours.append(int(m.group(1)) if m and 0 <= int(m.group(1)) <= 23 else None)
    return hours


def parse_day(value, default_month: str | None = None) -> date | None:
    """
    '01.11.2026', '01.11', '2026-11-01', datetime → date.
    default_month — 'YYYY-MM' для ячеек без года.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip().split(" ")[0]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = DATE_RE.match(text)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    if year is not None and year < 100:
        year += 2000
    if year is None:
        year = int(default_month[:4]) if default_month else date.today().year
    if not 1 <= month <= 12:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass
class Slot:
    day: date
    hour: int
    status: str          # busy | free | uncertain
    source: str = "color"


def build_slots(header: list, rows: list[list], default_month: str | None = None,
                color_index: int = 1) -> list[Slot]:
    """
    Сетка → слоты. rows: строки, где [0] — дата, дальше часы.
    color_index — с какого столбца начинаются часы.
    Возвращает только окрашенные ячейки: пустая = «не отмечено», не слот.
    """
    hours = parse_hour_header(header[color_index:])
    slots: list[Slot] = []
    for row in rows:
        if not row:
            continue
        day = parse_day(row[0], default_month)
        if day is None:
            continue
        for i, hour in enumerate(hours):
            if hour is None:
                continue
            col = color_index + i
            if col >= len(row):
                continue
            cell = row[col]
            color = cell.get("color") if isinstance(cell, dict) else None
            value = cell.get("value") if isinstance(cell, dict) else cell
            status = classify_color(color)
            if status is None and value not in (None, ""):
                legacy = str(value).strip()
                if legacy in ("1", "0") and not isinstance(color, dict):
                    status = "free" if legacy == "1" else "busy"
            if status:
                slots.append(Slot(day=day, hour=hour, status=status))
    return slots


TEXT_RULES = (
    ("uncertain", ("не понятно", "непонятно", "не знаю", "возможно", "наверное", "уточню")),
    ("free", ("свобод", "могу", "легко")),
    ("busy", ("занят", "не будет", "уезжа", "работа", "смена")),
)
HOUR_IN_TEXT = re.compile(r"(?:до|после|с)\s*(\d{1,2})(?:[:.]\d{2})?")

# люди пишут словами: «свободен после семи», «занята до восьми»
HOUR_WORDS = {
    "час": 13, "двух": 14, "трёх": 15, "трех": 15, "четырёх": 16, "четырех": 16,
    "пяти": 17, "шести": 18, "семи": 19, "восьми": 20, "девяти": 21,
    "десяти": 22, "одиннадцати": 23, "полудня": 12, "полдня": 12,
}
HOUR_WORD_RE = re.compile(r"(?:до|после|с)\s+([а-яё]+)")
RANGE_RE = re.compile(r"с\s*(\d{1,2})(?:[:.]\d{2})?\s*(?:до|по)\s*(\d{1,2})(?:[:.]\d{2})?")


def parse_range(text: str) -> tuple[int, int] | None:
    """«занята с 14 до 22» → (14, 22). Понимает и «с 14:00 по 22:00»."""
    match = RANGE_RE.search(str(text or "").lower())
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end == 0:                      # «до 24» пишут как «до 0»
        end = 24
    if not (0 <= start < end <= 24):
        return None
    return start, end


def status_from_text(value) -> tuple[str | None, int | None, bool]:
    """
    Текст из человеческой таблицы → (статус, час-граница, «после»).
    'занята до 19:00'      → ('busy', 19, False)  — занята до 19, дальше свободна
    'свободно с 19:00'     → ('free', 19, True)   — свободна с 19
    'пока не понятно'      → ('uncertain', None, False)
    """
    if value in (None, ""):
        return None, None, False
    text = str(value).strip().lower()
    if not text:
        return None, None, False

    status = None
    for name, keys in TEXT_RULES:
        if any(k in text for k in keys):
            status = name
            break
    if status is None:
        return "uncertain", None, False        # непонятный текст — не угадываем

    m = HOUR_IN_TEXT.search(text)
    hour = int(m.group(1)) if m else None
    if hour is None:                  # «после семи» — ищем слово
        word = HOUR_WORD_RE.search(text)
        if word:
            hour = HOUR_WORDS.get(word.group(1))
    after = ("после" in text) or ("с " in text and "до" not in text)
    if "до" in text and "после" not in text:
        after = False
    return status, hour, after


def hours_from_text(value, start_hour: int = 8, end_hour: int = 23) -> dict[int, str]:
    """Текст одного дня → карта час → статус."""
    span = parse_range(value)
    if span:
        status, _, _ = status_from_text(value)
        if status in ("busy", "free"):
            start, end = span
            busy_inside = status == "busy"
            return {
                h: ("busy" if (start <= h < end) == busy_inside else "free")
                for h in range(start_hour, end_hour + 1)
            }

    status, hour, after = status_from_text(value)
    if status is None:
        return {}
    out: dict[int, str] = {}
    if hour is None or status == "uncertain":
        return {h: status for h in range(start_hour, end_hour + 1)}

    if status == "free":
        # свободен с N: до N — занят; свободен до N: до N свободен, дальше неизвестно
        for h in range(start_hour, end_hour + 1):
            if after:
                out[h] = "free" if h >= hour else "busy"
            else:
                out[h] = "free" if h < hour else "uncertain"
        return out

    # busy
    for h in range(start_hour, end_hour + 1):
        if after:
            out[h] = "busy" if h >= hour else "free"
        else:
            out[h] = "busy" if h < hour else "free"
    return out


def parse_vertical_grid(dates_row: list, hour_rows: list[list],
                        color_index: int = 1) -> list[Slot]:
    """
    Сетка «часы в строках, даты в столбцах» (docs/development/22).
    dates_row: [подпись, дата1, дата2, ...]; hour_rows: [[час, ячейка, ячейка...], ...].
    """
    days = []
    for value in dates_row[color_index:]:
        if isinstance(value, dict):          # ячейка из Google API
            value = value.get("value")
        days.append(parse_day(value))
    slots: list[Slot] = []
    for row in hour_rows:
        if not row:
            continue
        hour_value = row[0]
        if isinstance(hour_value, dict):      # ячейка из Google API
            hour_value = hour_value.get("value")
        try:
            hour = int(str(hour_value).split(":")[0])
        except (ValueError, TypeError):
            continue
        if not 0 <= hour <= 23:
            continue
        for i, day in enumerate(days):
            if day is None:
                continue
            col = color_index + i
            if col >= len(row):
                continue
            cell = row[col]
            color = cell.get("color") if isinstance(cell, dict) else None
            value = cell.get("value") if isinstance(cell, dict) else cell
            status = classify_color(color)
            if status is None and value not in (None, ""):
                text = str(value).strip()
                if text in ("1", "0"):
                    status = "busy" if text == "1" else "free"
                else:
                    status, _, _ = status_from_text(text)
            if status:
                slots.append(Slot(day=day, hour=hour, status=status))
    return slots




def merge_intervals(slots: list[Slot]) -> list[dict]:
    """Слоты → читаемые интервалы: 18:00–22:00 занят."""
    grouped: dict[tuple[date, str], list[int]] = {}
    for s in slots:
        grouped.setdefault((s.day, s.status), []).append(s.hour)

    out = []
    for (day, status), hours in sorted(grouped.items()):
        hours = sorted(set(hours))
        start = prev = hours[0]
        for h in hours[1:] + [None]:
            if h is not None and h == prev + 1:
                prev = h
                continue
            out.append({"day": day.isoformat(), "from": start, "to": prev + 1,
                        "status": status, "title": STATUS_TITLES.get(status, status)})
            if h is not None:
                start = prev = h
    return out


def summary(slots: list[Slot]) -> dict:
    counts = {"busy": 0, "free": 0, "uncertain": 0}
    for s in slots:
        counts[s.status] = counts.get(s.status, 0) + 1
    days = {s.day for s in slots}
    return {"cells": len(slots), "days": len(days), **counts}

# --- разграничение «пусто» и «свободно» (docs/development/22 §7) ------------------------

FILLED_UNTIL_RE = re.compile(
    r"заполнено\s*(?:до|по)\s*[:.]?\s*(\d{1,2})[.,/\-](\d{1,2})(?:[.,/\-](\d{2,4}))?",
    re.IGNORECASE)


def parse_filled_until(text: str | None, today: date | None = None) -> date | None:
    """
    Читает пометку «Заполнено до: 30.11.2026» из шапки страницы.

    Зачем: пустая клетка и зелёная клетка — разные вещи, но ещё нужен ответ на вопрос
    «до какого числа человек вообще смотрел календарь». Без этой пометки
    «весь месяц зелёный» неотличим от «месяц не заполнял».
    """
    if not text:
        return None
    match = FILLED_UNTIL_RE.search(str(text))
    if not match:
        return None

    day, month = int(match.group(1)), int(match.group(2))
    year_text = match.group(3)
    base_year = (today or date.today()).year
    year = int(year_text) if year_text else base_year
    if year < 100:
        year += 2000
    try:
        result = date(year, month, day)
    except ValueError:
        return None

    # «заполнено до 05.01» в декабре — это уже следующий год
    if year_text is None and result < (today or date.today()):
        try:
            result = date(year + 1, month, day)
        except ValueError:
            return None
    return result


def within_marker(day: date, filled_until: date | None) -> bool:
    """Дата попадает в подтверждённый человеком период."""
    return filled_until is None or day <= filled_until


def split_by_marker(slots: list, filled_until: date | None) -> tuple[list, list]:
    """
    Делит слоты на «подтверждённые» и «за пределами пометки».
    Вторые нельзя считать ни занятыми, ни свободными — это «не отмечено».
    """
    inside, outside = [], []
    for slot in slots:
        (inside if within_marker(slot.day, filled_until) else outside).append(slot)
    return inside, outside

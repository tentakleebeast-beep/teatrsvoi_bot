"""
Разбор фразы назначения события (docs/architecture/51).

Организатор пишет коротко: «назначь репетицию с Витей и Людой в четверг в 20:00».
Здесь — только разбор текста: что за событие, кто, когда, сколько.

Имена ищем как **имена актёров**, а не роли: «Витя» — человек, «Сэм» — роль.
Поэтому роль подставляется, только если имени не нашлось (docs/architecture/41).

Модуль чистый: никаких обращений к БД и календарю.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .rules import WEEKDAYS  # единый справочник дней недели

KINDS = {
    "репетиц": "rehearsal", "прогон": "rehearsal", "репет": "rehearsal",
    "показ": "show", "спектакл": "show", "премьер": "show",
    "тренинг": "training", "тренировк": "training", "мастер-класс": "training",
    "аренд": "rental", "сдаём": "rental", "сдаем": "rental",
    "встреч": "meeting", "сбор": "meeting", "собрание": "meeting",
}

DEFAULT_DURATION = 2

_STOP_WORDS = {
    "назначь", "назначить", "поставь", "запланируй", "создай", "добавь",
    "событие", "встречу", "пожалуйста", "на", "в", "во", "и", "с", "со",
    "часов", "часа", "час", "минут", "утра", "вечера", "дня", "ночи",
}


@dataclass
class Assignment:
    kind: str | None = None
    weekday: int | None = None
    relative_days: int | None = None
    day: str | None = None              # явная дата «5.11» → «2026-11-05»
    hour: int | None = None
    duration_hours: int = DEFAULT_DURATION
    names: list[str] = field(default_factory=list)
    venue: str | None = None
    title: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Всё ли названо. День — это и день недели, и «завтра», и дата числом:
        требовать один `weekday` значило бы врать про полноту разбора."""
        has_day = (self.weekday is not None or self.relative_days is not None
                   or bool(self.day))
        return bool(self.kind and has_day and self.hour is not None)


_ENDINGS = ("ым", "им", "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ой", "ей",
            "ом", "ем", "ах", "ях", "ам", "ям", "ы", "и", "а", "я", "у", "ю",
            "е", "о", "й", "ь")


def name_stem(word: str) -> str:
    """
    Основа имени без падежного окончания: «Витей» → «вит», «Витя» → «вит»,
    «Людой» → «люд», «Люда» → «люд». Так «с Витей и Людой» находит людей.
    """
    low = _norm(word).strip()
    for ending in _ENDINGS:
        if len(low) - len(ending) >= 3 and low.endswith(ending):
            return low[: -len(ending)]
    return low


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


# --- часы: «с 15-00 до 18-00», «15-18», «с трёх до шести» ---------------------
# Люди пишут время так, как говорят: через дефис вместо двоеточия и словами.
# Живой диалог 05.11.2026 сорвался именно здесь: «с 15-00 до 18-00» бот не понял
# и отдал «15» бюджету как сумму.

_HOUR_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "трех": 3,
    "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6,
    "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11,
    "двенадцать": 12, "двенадцати": 12, "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
    "двадцать": 20, "двадцати": 20,
}

_WORD = re.compile(r"\b[а-я]+\b")
#: Явная дата числом. Дробь рядом с «час» датой не считается: «завтра на 1.5 часа»
#: должно остаться завтрашним днём, а не уехать в 1 мая (дефект живого диалога).
_DATE = re.compile(r"(?<![\d.,])\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b"
                   r"(?!\s*(?:час|мин|ч\.))")
_DASH_MINUTES = re.compile(r"(?<!\d)(\d{1,2})-(\d{2})(?!\d)")
_SPAN = re.compile(r"(?:\bс\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:до|-)\s*(\d{1,2})(?::(\d{2}))?")


def _word_hours(text: str) -> str:
    """«с трёх до шести» → «с 3 до 6»: часы, записанные словами."""
    return _WORD.sub(
        lambda m: str(_HOUR_WORDS[m.group(0)]) if m.group(0) in _HOUR_WORDS else m.group(0),
        text)


def normalize_hours(text: str) -> str:
    """Дефис в часах → двоеточие, часы словами → цифрами.

    «15-00» — это 15:00 (минуты), «15-18» — интервал (18 тоже час): различаем по
    правой части. «с 15-00 до 18-00» после этого разбирается как обычный интервал.
    """
    low = _norm(text).replace("–", "-").replace("—", "-")
    low = _DASH_MINUTES.sub(
        lambda m: f"{m.group(1)}:{m.group(2)}"
        if int(m.group(2)) > 23 or int(m.group(2)) < 10 else m.group(0), low)
    return _word_hours(low)


def hour_span(text: str) -> tuple[int, int] | None:
    """Интервал часов: «с 15-00 до 18-00», «15-18», «с трёх до шести» → (15, 18)."""
    match = _SPAN.search(normalize_hours(text))
    if not match:
        return None
    try:
        begin, finish = int(match.group(1)), int(match.group(3))
    except (TypeError, ValueError):
        return None
    return (begin, finish) if 0 <= begin < finish <= 24 else None


def parse_assignment(text: str, today: date | None = None) -> Assignment:
    """Разбирает фразу назначения. Возвращает то, что понял, и чего не хватает."""
    low = normalize_hours(text)
    current = today or datetime.now().date()
    result = Assignment()

    for stem, kind in KINDS.items():
        if stem in low:
            result.kind = kind
            break

    # явная дата: «5.11», «05.11.2026» — иначе «завтра» и день недели.
    # Явная дата сильнее относительной: если названы обе, берётся точная.
    explicit = _DATE.search(low)
    if explicit:
        day_number, month = int(explicit.group(1)), int(explicit.group(2))
        year = int(explicit.group(3) or current.year)
        if year < 100:
            year += 2000
        try:
            result.day = date(year, month, day_number).isoformat()
        except ValueError:
            result.day = None

    # относительные даты: «сегодня», «завтра», «послезавтра»
    relative = re.search(r"\b(послезавтра|завтра|сегодня)\b", low)
    if relative:
        result.relative_days = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[relative.group(1)]
    else:
        for word, index in WEEKDAYS.items():
            if re.search(rf"\b{word}\b", low):
                result.weekday = index
                break

    # время: интервал «с 15-00 до 18-00» / «15-18» / «с трёх до шести»,
    # иначе начало «в 20:00», «в 20», «к 19:30»
    span = hour_span(low)
    if span:
        result.hour, end_hour = span
        result.duration_hours = end_hour - result.hour
    else:
        match = re.search(r"\b(?:в|к)\s*(\d{1,2})(?::(\d{2}))?\b", low)
        if match:
            hour = int(match.group(1))
            if 0 <= hour <= 23:
                result.hour = hour

        # длительность: «на 2 часа», «2 часа», «до 22:00». Дробь сюда не входит:
        # в «1.5 часа» пятёрка идёт после точки и означает половину, а не пять часов.
        duration = re.search(r"(?<![.,\d])(?:на\s+)?(\d{1,2})\s*час", low)
        if duration:
            result.duration_hours = max(1, min(8, int(duration.group(1))))
        else:
            until = re.search(r"\bдо\s*(\d{1,2})(?::\d{2})?\b", low)
            if until and result.hour is not None:
                end = int(until.group(1))
                if end > result.hour:
                    result.duration_hours = end - result.hour

    # помещение
    for code, words in (("stage", ("сцен",)), ("dressing", ("гримерк", "гримёрк")),
                        ("backstage", ("подсобк",))):
        if any(word in low for word in words):
            result.venue = code
            break

    # кто: «с Витей и Людой», «для Саши и Алёны». Точку в классе не запрещаем:
    # «с Марком и Машей 5.11» — дата числом идёт после имён, и обрыв на её точке
    # терял участников (дефект живого диалога 05.11.2026).
    names_part = re.search(r"(?:с|со|для)\s+([^,;]+?)(?:\s+(?:в|во|к)\s+\d|\s+на\s+\d|$)",
                           low)
    if names_part:
        chunk = names_part.group(1)
        # отрезаем хвост, если в него попали день недели или время. Кроме «в 20:00»
        # это ещё «завтра» и интервал «с 15:00»: диалог дописывает уточнения к той же
        # фразе (карта t_ebce081e), и хвост не должен становиться именем участника.
        tail = re.search(r"\s+(?:в|во|к|на|с|со)\s+(?:\d{1,2}\b|"
                         + "|".join(WEEKDAYS) + r")"
                         r"|\s+(?:завтра|сегодня|послезавтра)\b"
                         r"|\s+\d{1,2}[.\-/]\d{1,2}\b", chunk)
        if tail:
            chunk = chunk[:tail.start()]
        for raw in re.split(r"\s*(?:,| и )\s*", chunk):
            candidate = raw.strip().strip(".,;:!?»«\"'")
            if not candidate or candidate in _STOP_WORDS:
                continue
            if re.fullmatch(r"\d.*", candidate):
                continue
            result.names.append(candidate)

    if not result.kind:
        result.problems.append("kind")
    if result.weekday is None and result.relative_days is None and not result.day:
        result.problems.append("weekday")
    if result.hour is None:
        result.problems.append("hour")
    return result


def describe(assignment: Assignment, weekday_names: list[str]) -> str:
    """Короткое описание для карточки подтверждения."""
    kind_titles = {"rehearsal": "Репетиция", "show": "Показ", "training": "Тренинг",
                   "rental": "Аренда", "meeting": "Встреча"}
    parts = [kind_titles.get(assignment.kind or "", "Событие")]
    if assignment.weekday is not None:
        parts.append(weekday_names[assignment.weekday])
    if assignment.hour is not None:
        parts.append(f"{assignment.hour:02d}:00–{(assignment.hour + assignment.duration_hours) % 24:02d}:00")
    if assignment.names:
        parts.append("с " + ", ".join(assignment.names))
    return " ".join(parts)

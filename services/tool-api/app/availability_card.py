"""
Карточка дня занятости: состояние, две границы, комментарий (FR-C9).

Модель одна на ввод, хранение и запись в лист. Занятость дня — это интервал и
состояние, а пустая граница означает край дня (08:00 и 24:00). Поэтому
«занят до 19:00» и «свободен с 19:00» — одно и то же, сказанное с двух сторон:
человек выбирает состояние и, если нужно, границу.

Границы хранятся в минутах от полуночи, а не в часах: в листе есть «до 18:30»,
и потеря получаса — это не косметика, а ложное «свободен» на 18:00–19:00.

Материализация дня идёт с округлением в сторону занятости: занятый интервал
расширяется до целых часов, свободный сужается. Если свободное окно после
сужения исчезает (меньше часа), часы помечаются `uncertain`, а не `free`:
бот не должен обещать время, за которое не может поручиться.

Модуль чистый: ни HTTP, ни БД, ни Google.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schedule_grid import HOUR_WORDS, TEXT_RULES

DAY_START = 8 * 60        # 08:00 — начало рабочего дня (решение C-5)
DAY_END = 24 * 60         # 24:00 — конец дня

STATUSES = ("free", "busy", "uncertain")
WHOLE_DAY = ("весь день", "весь", "сутки")

_SPACES = re.compile(r"\s+")
_PARENS = re.compile(r"\(([^)]*)\)")
# «с 16 до 18:30», «с 14:00 по 22:00»
_RANGE = re.compile(r"с\s*(\d{1,2})(?:[:.\-](\d{2}))?\s*(?:до|по)\s*(\d{1,2})(?:[:.\-](\d{2}))?")
# «до 19:00», «после 19», «с 19.30»
_BOUND = re.compile(r"(до|после|с|по)\s*(\d{1,2})(?:[:.\-](\d{2}))?")
# «после семи»
_WORD_BOUND = re.compile(r"(до|после|с|по)\s+([а-яё]+)")
_SHORT_TAIL = re.compile(r"^[а-яё]{1,2}\b")
_TRIM = " \t,.;:—-–"


class DayCardError(ValueError):
    """Некорректная карточка дня: код причины в `args[0]`."""


@dataclass(frozen=True)
class DayCard:
    """Занятость одного дня. `from_min`/`to_min` — минуты от полуночи или None."""

    status: str
    from_min: int | None = None
    to_min: int | None = None
    note: str = ""
    exact: bool = True          # False — в данных больше смен статуса, чем выражает карточка

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise DayCardError("bad_status")
        if self.status == "uncertain":
            if self.from_min is not None or self.to_min is not None:
                raise DayCardError("bad_bounds")
            return
        for name, value in (("from", self.from_min), ("to", self.to_min)):
            if value is not None and not 0 <= value <= DAY_END:
                raise DayCardError(f"bad_{name}")
        if self.from_min is not None and self.to_min is not None:
            if self.from_min >= self.to_min:
                raise DayCardError("bad_interval")

    @property
    def is_whole_day(self) -> bool:
        return self.status != "uncertain" and self.from_min is None and self.to_min is None


def _clock(hour: str, minute: str | None) -> int:
    return int(hour) * 60 + (int(minute) if minute else 0)


def _status_keyword(text: str) -> tuple[str, str | None]:
    """
    Первое совпадение со словарём состояний. Порядок важен: uncertain → free → busy.

    Ключ ищется только с начала слова: иначе «могу» срабатывает внутри «смогу»,
    и «занята до 16:00, но если очень надо, смогу» превращается в «свободна».
    """
    for name, keys in TEXT_RULES:
        if name != "uncertain":
            continue
        for key in keys:
            if re.search(rf"(?<![а-яё]){re.escape(key)}", text):
                return "uncertain", key

    # иначе берём то состояние, которое человек назвал первым:
    # «занята до 22, дальше могу» — это занята до 22, а не «свободна»
    found: list[tuple[int, str, str]] = []
    for name, keys in TEXT_RULES:
        if name == "uncertain":
            continue
        for key in keys:
            match = re.search(rf"(?<![а-яё]){re.escape(key)}", text)
            if match:
                found.append((match.start(), name, key))
                break
    if found:
        _, name, key = min(found)
        return name, key
    return "uncertain", None


def parse(value) -> DayCard | None:
    """Текст из человеческой таблицы → карточка дня. Пустая клетка → None."""
    if value is None:
        return None
    # регистр сохраняем для комментария («в Самаре»), а для разбора времени он не нужен
    original = _SPACES.sub(" ", str(value).strip())
    text = original.lower()
    if not text:
        return None

    status, keyword = _status_keyword(text)
    notes = [m.group(1).strip() for m in _PARENS.finditer(original) if m.group(1).strip()]
    body = _PARENS.sub(" ", text)
    if keyword:
        body = body.replace(keyword, " ", 1)
    for marker in WHOLE_DAY:
        body = body.replace(marker, " ")

    from_min = to_min = None
    matched = False
    range_match = _RANGE.search(body)
    if range_match:
        from_min = _clock(range_match.group(1), range_match.group(2))
        to_min = _clock(range_match.group(3), range_match.group(4))
        body = body.replace(range_match.group(0), " ")
        matched = True
    else:
        bound = _BOUND.search(body)
        if bound:
            hour = _clock(bound.group(2), bound.group(3))
            from_min, to_min = (
                (hour, None) if bound.group(1) in ("после", "с") else (None, hour)
            )
            body = body.replace(bound.group(0), " ")
            matched = True
        else:
            word = _WORD_BOUND.search(body)
            if word and word.group(2) in HOUR_WORDS:
                hour = HOUR_WORDS[word.group(2)] * 60
                from_min, to_min = (
                    (hour, None) if word.group(1) in ("после", "с") else (None, hour)
                )
                body = body.replace(word.group(0), " ")
                matched = True

    # «занят?» — это сомнение человека, а не занятость: не угадываем
    if "?" in text:
        status, from_min, to_min = "uncertain", None, None

    # если граница распознана, остаток текста — комментарий человека:
    # ведущее «а»/«но» от предыдущей части фразы в комментарий не берём
    tail = _SHORT_TAIL.sub(" ", body.strip(_TRIM)).strip(_TRIM) if matched else ""
    if tail:
        notes.append(tail)
    return DayCard(status=status, from_min=from_min, to_min=to_min, note="; ".join(notes))


def to_text(card: DayCard, *, female: bool = False) -> str:
    """Карточка дня → каноническая запись для листа."""
    if card.status == "uncertain":
        text = "пока не понятно"
    else:
        if card.status == "busy":
            word = "занята" if female else "занят"
        else:
            word = "свободна" if female else "свободен"
        low, high = card.from_min, card.to_min
        if low is None and high is None:
            text = word
        elif low is None:
            text = f"{word} до {_format(high)}"
        elif high is None:
            text = f"{word} с {_format(low)}"
        else:
            text = f"{word} с {_format(low)} до {_format(high)}"
    if card.note:
        return f"{text} ({card.note})"
    return text


def _format(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def to_hours(
    card: DayCard,
    day_start: int = DAY_START,
    day_end: int = DAY_END,
) -> dict[int, str]:
    """
    Карточка дня → карта час → статус на весь день, без «дыр».

    День материализуется целиком, чтобы «свободен» и «данных нет» оставались
    разными вещами: неотмеченный день — это отсутствие строк, а отмеченный —
    полный набор часов с явным дополнением интервала.
    """
    first, last = day_start // 60, day_end // 60
    if card.status == "uncertain":
        return {hour: "uncertain" for hour in range(first, last)}

    start = day_start if card.from_min is None else card.from_min
    end = day_end if card.to_min is None else card.to_min
    result: dict[int, str] = {}

    if card.status == "busy":
        # занятый интервал расширяем наружу: лучше «занят», чем «свободен» по ошибке
        wide_start = start - start % 60
        wide_end = end + (60 - end % 60) % 60
        for hour in range(first, last):
            hour_start, hour_end = hour * 60, hour * 60 + 60
            inside = hour_start < wide_end and hour_end > wide_start
            result[hour] = "busy" if inside else "free"
        return result

    # свободный интервал сужаем внутрь
    narrow_start = start + (60 - start % 60) % 60
    narrow_end = end - end % 60
    for hour in range(first, last):
        hour_start, hour_end = hour * 60, hour * 60 + 60
        if narrow_start < narrow_end and hour_start >= narrow_start and hour_end <= narrow_end:
            result[hour] = "free"
        elif hour_end <= start or hour_start >= end:
            result[hour] = "busy"
        else:
            result[hour] = "uncertain"
    return result


def parse_clock(value: str) -> int:
    """«18:30», «18.30», «18-30», «18» → минуты от полуночи. Иначе DayCardError('bad_time')."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2})(?:[:.\-](\d{2}))?", text)
    if not match:
        raise DayCardError("bad_time")
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    total = hour * 60 + minute
    if minute > 59 or total > DAY_END:
        raise DayCardError("bad_time")
    return total


def from_hours(
    hours: dict[int, str],
    note: str = "",
    day_start: int = DAY_START,
    day_end: int = DAY_END,
) -> DayCard | None:
    """
    Карта час → статус обратно в карточку дня.

    Обратное преобразование к `to_hours`: нужно, чтобы API читал то же, что записал,
    и чтобы занятость из файла или сетки можно было показать одной строкой.
    Если смен статуса больше, чем выражает карточка (два отдельных окна), берём самый
    длинный отрезок и помечаем карточку `exact = False` — человеку покажем как есть,
    но честно скажем, что день описан приблизительно.
    """
    if not hours:
        return None
    ordered = [(hour, hours[hour]) for hour in sorted(hours)]
    if all(status == "uncertain" for _, status in ordered):
        return DayCard("uncertain", note=note)

    runs: list[list] = []                       # [начало, конец, статус] в минутах
    for hour, status in ordered:
        start, end = hour * 60, hour * 60 + 60
        if runs and runs[-1][2] == status and runs[-1][1] == start:
            runs[-1][1] = end
        else:
            runs.append([start, end, status])
    pure = [run for run in runs if run[2] in ("free", "busy")]

    if not pure:
        return DayCard("uncertain", note=note)
    if len(pure) == 1:
        return DayCard(pure[0][2], None, None, note)
    if len(pure) == 2:
        first, second = pure
        if first[0] == day_start:
            return DayCard(first[2], None, first[1], note)
        return DayCard(second[2], second[0], None, note)
    if len(pure) == 3:
        middle = pure[1]
        return DayCard(middle[2], middle[0], middle[1], note)
    longest = max(pure, key=lambda run: run[1] - run[0])
    return DayCard(longest[2], longest[0], longest[1], note, exact=False)


def to_minutes_text(minute: int | None) -> str | None:
    """Минуты → «18:30» для API. None остаётся None (граница открыта)."""
    return None if minute is None else _format(minute)

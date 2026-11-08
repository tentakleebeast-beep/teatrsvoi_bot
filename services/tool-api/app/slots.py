"""
Поиск окон в расписании (docs/architecture/40, docs/architecture/17 §3.2).

Чистые функции: на вход — отметки занятости, на выход — окна с пояснениями.
Ни БД, ни сети, ни модели: окна считает код, LLM только пересказывает.

Область этого модуля — участники студии (репетиция, тренинг, показ, встреча).
Субаренда (зал и арендаторы) считается в `app/venue.py`; обе области живут под
одним действием С6 и по одной сетке часов (docs/architecture/40 §2).

Семантика трёх состояний (docs/development/22 §7):
  * True  — человек отметил «свободен»;
  * False — отметил «занят» или «не уверен»;
  * отсутствие отметки — «не знаю», а НЕ «занят».

Из этого следует главное правило: окно строится только там, где никто не занят,
а люди без отметок попадают в список «требует подтверждения», но окно не блокируют.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .rules import WORK_DAY_END, WORK_DAY_START

# Сетка одна на весь поиск окон: часы WORK_DAY_START…WORK_DAY_END-1, граница
# «до» не включается. У листа занятости 16 клеток 08:00…23:00 (docs/22 §1),
# последняя — 23:00–24:00; отметка за 23:00 хранится и должна учитываться.
DAY_START = WORK_DAY_START
DAY_END = WORK_DAY_END

FREE, BUSY, UNKNOWN = "free", "busy", "unknown"


@dataclass(frozen=True)
class Window:
    day: date
    from_hour: int
    to_hour: int                       # не включается: 19–21 значит 19:00–21:00
    unknown: tuple[int, ...] = ()      # участники без отметок в этом блоке

    @property
    def hours(self) -> int:
        return self.to_hour - self.from_hour

    @property
    def score(self) -> tuple[int, int, bool, int]:
        """
        Детерминированный порядок без модели:
        меньше неизвестных → длиннее окно → вечер → более ранняя дата.
        """
        evening = self.from_hour >= 18
        return (-len(self.unknown), self.hours, evening, -self.day.toordinal())

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "from_hour": self.from_hour,
                "to_hour": self.to_hour, "hours": self.hours,
                "unknown": list(self.unknown)}


# marks: {participant_id: {(date, hour): bool} | None}
Marks = dict[int, dict[tuple[date, int], bool] | None]


def _status(marks: Marks, pid: int, day: date, hour: int) -> str:
    table = marks.get(pid)
    if not table:
        return UNKNOWN
    value = table.get((day, hour))
    if value is None:
        return UNKNOWN
    return FREE if value else BUSY


def find_windows(marks: Marks,
                 days: list[date],
                 min_hours: int = 2,
                 day_start: int = DAY_START,
                 day_end: int = DAY_END,
                 limit: int = 3) -> list[Window]:
    """
    Ищет до `limit` лучших окон длиной не меньше `min_hours`.

    Окно (docs/architecture/40 §2) — непрерывный блок часов внутри рабочего дня,
    где в каждом часе никто из участников не занят и хотя бы один явно свободен.
    Час, про который не сказал никто, окном не становится: пустая клетка не
    запрещает, но и не подтверждает.

    Участники без отметок перечисляются в `unknown` — это не запрет, а
    предупреждение. Список считается по всему блоку: человек, промолчавший
    про один час окна, не подтвердил окно целиком, даже если про соседний
    час он ответил.

    `min_hours` меньше часа — ошибка вызова, а не «окон нет»: тихий пустой
    ответ на неверный критерий выглядит как отсутствие времени у людей.
    """
    if int(min_hours) < 1:
        raise ValueError("min_hours: окно короче часа не бывает (docs/architecture/40 §2)")

    day_start, day_end = max(0, int(day_start)), min(24, int(day_end))
    if not marks or not days or day_start >= day_end:
        return []

    everyone = sorted(marks)
    if not any(table for table in marks.values()):
        return []                     # данных нет ни по кому — окна выдумывать нельзя

    hours = list(range(day_start, day_end))
    if not hours:
        return []

    windows: list[Window] = []

    for day in days:
        run_start: int | None = None
        run_unknown: set[int] = set()

        for hour in hours:
            statuses = {pid: _status(marks, pid, day, hour) for pid in everyone}
            values = statuses.values()
            # час годится, только если никто не занят и кто-то ЯВНО свободен
            if BUSY in values or FREE not in values:
                if run_start is not None:
                    _maybe_add(windows, day, run_start, hour, min_hours, run_unknown)
                    run_start, run_unknown = None, set()
                continue

            if run_start is None:
                run_start = hour
                run_unknown = set()

            # множество «молчавших» только растёт: подтверждение одного часа
            # не отменяет молчания про другой час того же окна
            for pid, status in statuses.items():
                if status == UNKNOWN:
                    run_unknown.add(pid)

        if run_start is not None:
            _maybe_add(windows, day, run_start, hours[-1] + 1, min_hours, run_unknown)

    windows.sort(key=lambda w: w.score, reverse=True)
    return windows[:limit]


def _maybe_add(windows: list[Window], day: date, start: int, end: int,
               min_hours: int, unknown: set[int]) -> None:
    if end - start >= min_hours:
        windows.append(Window(day=day, from_hour=start, to_hour=end,
                              unknown=tuple(sorted(unknown))))


def longest_window(marks: Marks, days: list[date],
                   day_start: int = DAY_START, day_end: int = DAY_END) -> Window | None:
    """Самое длинное окно в диапазоне."""
    found = find_windows(marks, days, min_hours=1, day_start=day_start,
                         day_end=day_end, limit=1_000)
    return max(found, key=lambda w: w.hours) if found else None


def describe(window: Window, names: dict[int, str] | None = None) -> str:
    """Человеческая строка: «вторник 19:00–21:00 (2 ч), нет данных: Лебедева»."""
    names = names or {}
    weekday = ["понедельник", "вторник", "среда", "четверг",
               "пятница", "суббота", "воскресенье"][window.day.weekday()]
    text = (f"{weekday} {window.day:%d.%m} "
            f"{window.from_hour:02d}:00–{window.to_hour:02d}:00 ({window.hours} ч)")
    if window.unknown:
        listed = ", ".join(names.get(pid, str(pid)) for pid in window.unknown)
        text += f", нет данных: {listed}"
    return text


def coverage(marks: Marks, day: date, hour: int) -> tuple[int, int, int]:
    """(свободны, заняты, без отметки) в конкретный час."""
    counts = {FREE: 0, BUSY: 0, UNKNOWN: 0}
    for pid in marks:
        counts[_status(marks, pid, day, hour)] += 1
    return counts[FREE], counts[BUSY], counts[UNKNOWN]


def week_days(start: date) -> list[date]:
    """Семь дней начиная с даты."""
    return [start + timedelta(days=offset) for offset in range(7)]


@dataclass(frozen=True)
class RankedHour:
    """Час с наибольшим числом свободных — режим «когда больше всего людей»."""
    day: date
    hour: int
    free_cnt: int
    marked_cnt: int
    free_ids: tuple[int, ...] = ()
    busy_ids: tuple[int, ...] = ()

    @property
    def share(self) -> float:
        return self.free_cnt / self.marked_cnt if self.marked_cnt else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "hour": self.hour,
                "free": self.free_cnt, "marked": self.marked_cnt,
                "free_ids": list(self.free_ids), "busy_ids": list(self.busy_ids)}


def rank_hours(marks: Marks, days: list[date],
               day_start: int = DAY_START, day_end: int = DAY_END,
               min_free: int = 1, limit: int = 5) -> list[RankedHour]:
    """
    Лучшие часы по числу свободных людей (docs/architecture/41 §5).

    Нужен для тренигов и общих сборов: там важно не «все свободны», а «придёт
    максимум народу». Порядок: больше свободных → вечер → более ранняя дата.
    """
    if not marks or not days:
        return []

    hours = list(range(max(day_start, 0), min(day_end, 24)))
    ranked: list[RankedHour] = []

    for day in days:
        for hour in hours:
            free_ids, busy_ids = [], []
            for pid in sorted(marks):
                status = _status(marks, pid, day, hour)
                if status == FREE:
                    free_ids.append(pid)
                elif status == BUSY:
                    busy_ids.append(pid)
            if len(free_ids) < min_free:
                continue
            ranked.append(RankedHour(day=day, hour=hour, free_cnt=len(free_ids),
                                     marked_cnt=len(free_ids) + len(busy_ids),
                                     free_ids=tuple(free_ids), busy_ids=tuple(busy_ids)))

    ranked.sort(key=lambda r: (-r.free_cnt, -(r.hour >= 18), r.day.toordinal(), r.hour))
    return ranked[:limit]

"""
«Обычная неделя»: шаблон занятости по дням недели (FR-C10).

Люди заполняют одно и то же каждый месяц — работа, семья, тренировки. Шаблон
хранит состояние дня недели (0 = понедельник) и разворачивается в конкретные даты:
человек жмёт «заполнить по обычной неделе» и правит исключения.

Правила, которые нельзя размывать:
  * шаблон не перезаписывает уже отмеченные дни, если об этом не попросили явно;
  * «не знаю» в шаблоне — такое же значение, как «занят»: молчание не считается
    свободой;
  * шаблон хранит минуты, а не часы: 18:30 в шаблоне должно остаться 18:30.

Модуль чистый: ни БД, ни HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.availability_card import DayCard

WEEK_DAYS = 7


@dataclass(frozen=True)
class TemplateDay:
    """Один день недели в шаблоне."""

    weekday: int
    card: DayCard

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError("bad_weekday")


def from_week(cards: dict[date, DayCard]) -> list[TemplateDay]:
    """Неделя карточек → шаблон: по одному дню на каждый отмеченный день недели."""
    by_weekday: dict[int, DayCard] = {}
    for day, card in cards.items():
        by_weekday[day.weekday()] = card
    return [TemplateDay(weekday, by_weekday[weekday]) for weekday in sorted(by_weekday)]


def to_week(template: list[TemplateDay], first: date) -> dict[date, DayCard]:
    """Шаблон → карточки на неделю, начинающуюся с `first` (понедельник или любой день)."""
    monday = first - timedelta(days=first.weekday())
    out: dict[date, DayCard] = {}
    for item in template:
        out[monday + timedelta(days=item.weekday)] = item.card
    return out


def fill(
    first: date,
    template: list[TemplateDay],
    existing: dict[date, DayCard | None],
    overwrite: bool = False,
) -> tuple[dict[date, DayCard], list[date]]:
    """
    Что записать, заполняя неделю по шаблону.

    Возвращает карточки к записи и дни, которые пропущены, потому что уже отмечены.
    Без `overwrite` чужая работа (и своя прошлая) не затирается: человек правит
    исключения сам, а не обнаруживает, что шаблон съел его отметки.
    """
    week = to_week(template, first)
    write: dict[date, DayCard] = {}
    skipped: list[date] = []
    for day, card in sorted(week.items()):
        current = existing.get(day)
        if current is not None and not overwrite:
            skipped.append(day)
            continue
        write[day] = card
    return write, skipped


def describe(template: list[TemplateDay]) -> str:
    """Короткая подпись шаблона для человека: «7 дней: пн–пт занят до 19:00» — слишком
    грубо, поэтому просто «7 дней из 7». Считаем заполненные дни недели."""
    return f"{len(template)} из {WEEK_DAYS} дней"

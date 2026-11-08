"""
Покрытие занятости: можно ли верить своду (FR-C7).

Свод «кто свободен» строится по отметкам людей. Если отметилась половина группы,
свод молчит про остальных — и это худший вид ошибки: пустая клетка читается как
«свободен». Поэтому бот обязан сам сказать, сколько людей отчиталось, и прямо
назвать порог, ниже которого планировать нельзя.

Модуль чистый: на вход числа, на выход вердикт и строка для человека.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

THRESHOLD = 0.8          # ниже 80% отметившихся свод считается неполным
MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня",
                   "июля", "августа", "сентября", "октября", "ноября", "декабря")


@dataclass(frozen=True)
class Coverage:
    """Сколько людей отчиталось из скольких ожидались."""

    expected: int
    marked: int
    threshold: float = THRESHOLD
    not_connected: int = 0      # люди в статусе pending: в бота ещё не зашли

    @property
    def ratio(self) -> float:
        if self.expected <= 0:
            return 0.0
        return round(self.marked / self.expected, 3)

    @property
    def trustworthy(self) -> bool:
        return self.expected > 0 and self.ratio >= self.threshold

    @property
    def missing(self) -> int:
        return max(self.expected - self.marked, 0)

    def line(self) -> str:
        """Одна строка для человека: сколько отметилось и можно ли планировать."""
        if self.expected <= 0:
            return "Занятость: в студии нет активных людей — планировать не по кому."
        head = f"Занятость: отметились {self.marked} из {self.expected}"
        if self.trustworthy:
            line = f"{head} — своду можно верить."
        else:
            line = (f"{head} — данных мало, чтобы искать окна: "
                    f"не хватает {self.missing}, нужно не меньше "
                    f"{_percent(self.threshold)}.")
        if self.not_connected:
            word = plural(self.not_connected, "человек", "человека", "человек")
            line += (f" Ещё {self.not_connected} {word} не подключены к боту — "
                     f"их занятость неизвестна.")
        return line


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Склонение по числу: 1 человек, 2 человека, 5 человек."""
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def coverage(expected: int, marked: int, threshold: float = THRESHOLD,
             not_connected: int = 0) -> Coverage:
    """Покрытие недели: ожидаемые люди, отметившиеся и ещё не подключённые."""
    return Coverage(expected=max(int(expected), 0), marked=max(int(marked), 0),
                    threshold=threshold, not_connected=max(int(not_connected), 0))


def week_title(first: date, days: int = 7) -> str:
    """«09 — 15 ноября» — для строки о покрытии."""
    if not isinstance(first, date):
        return ""
    last = first + timedelta(days=days - 1)
    if first.month == last.month:
        return f"{first.day:02d} — {last.day:02d} {MONTHS_GENITIVE[first.month - 1]}"
    return (f"{first.day:02d} {MONTHS_GENITIVE[first.month - 1]} — "
            f"{last.day:02d} {MONTHS_GENITIVE[last.month - 1]}")

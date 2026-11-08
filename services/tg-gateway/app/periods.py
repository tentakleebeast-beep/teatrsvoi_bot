"""Периоды месяцев для кнопок панели (Ш-32).

Период — строка ``YYYY-MM``, как в ``budget_tx.period`` и в API
``/tools/budget/recent``. Раньше панель «Бюджет» умела показывать только
текущий месяц: посмотреть прошлые из бота было нельзя (обращение №15).

Разбор вынесен из ``main.py``, чтобы переход через границу года
(январь → декабрь) проверялся unit-тестом без поднятия гейтвея.
"""

from __future__ import annotations

import re

import texts

_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def is_period(value: str | None) -> bool:
    """True, если строка — корректный период ``YYYY-MM``."""
    return bool(value and _PERIOD_RE.match(value))


def shift_period(period: str, delta: int) -> str:
    """Сдвиг периода на ``delta`` месяцев с переходом через границу года.

    ``shift_period("2027-01", -1) == "2026-12"``; неверный период
    возвращается как есть, чтобы кнопка не строила мусорный callback.
    """
    if not is_period(period):
        return period
    year, month = (int(x) for x in period.split("-"))
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def money_nav_rows(period: str, first_period: str | None = None,
                   current_period: str | None = None) -> list[list[tuple[str, str]]]:
    """Кнопки панели «Бюджет»: соседние месяцы в границах журнала (Ш-43).

    Нижняя граница — первый период в budget_tx, верхняя — текущий месяц.
    На границе кнопка не рисуется: иначе можно уйти в пустой 2025 год
    (решение Виктора 28.10.2026, V-003). Без границ поведение прежнее.
    """
    base = period if is_period(period) else ""
    if is_period(base):
        prev_period = shift_period(base, -1)
        next_period = shift_period(base, 1)
        nav: list[tuple[str, str]] = []
        if not is_period(first_period) or prev_period >= first_period:
            nav.append(("Предыдущий месяц", f"panel:money:{prev_period}"))
        if not is_period(current_period) or next_period <= current_period:
            nav.append(("Следующий месяц", f"panel:money:{next_period}"))
    else:
        nav = [("Предыдущий месяц", "panel:money:"),
               ("Следующий месяц", "panel:money:")]
    return ([nav] if nav else []) + [[(texts.BTN_BACK, "panel:root")]]

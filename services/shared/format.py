"""Общие форматы обоих сервисов: деньги и даты по-русски (R-6).

Зачем один модуль: `money()` жил в трёх местах и уже разъехался — в боте
и уведомлениях «12 345 ₽» с обычным пробелом, на экранах Mini App
«12 345 ₽» с неразрывным. Один человек видел два разных вида одной суммы.
Здесь одна реализация и один выбор оформления через параметр.

Где используется:
  * services/tool-api — уведомления, каркас страниц Mini App;
  * services/tg-gateway — тексты бота (тариф, взнос, запись операции).

Импорт — `from services.shared.format import money`: корень репозитория должен
быть в `sys.path` (в контейнерах это делает рабочий каталог `/app`, в скриптах —
`scripts/_cli.py`, в тестах — `tests/conftest.py`).
"""

from __future__ import annotations

from datetime import date

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
MONTHS_NOM = ("январь", "февраль", "март", "апрель", "май", "июнь",
              "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг",
                 "пятница", "суббота", "воскресенье")

EMPTY = "—"


def money(value: float | int | None, *, design: bool = False, empty: str = EMPTY) -> str:
    """Сумма по-русски: разряды через пробел, знак рубля, без копеек.

    design=False — вид для сообщений бота: обычный пробел и дефис в минусе
    («-1 500 ₽»), так текст переносится и читается в любом клиенте.
    design=True — вид для экранов Mini App: неразрывные пробелы и типографский
    минус («−1 500 ₽»), сумма не рвётся по строкам.

    Пустое значение — прочерк: в таблицах «нет данных» и «ноль» различаются.
    """
    if value is None:
        return empty
    amount = round(float(value))
    space = "\u00a0" if design else " "
    sign = ""
    if amount < 0:
        sign = "−" if design else "-"
    return f"{sign}{abs(amount):,}".replace(",", space) + space + "₽"


def human_date(value: date | str | None, *, with_weekday: bool = True) -> str:
    """Дата словами: «31 октября, суббота». Не `2026-10-31`."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    text = f"{value.day} {MONTHS[value.month - 1]}"
    if with_weekday:
        text += f", {WEEKDAYS_FULL[value.weekday()]}"
    return text


def human_month(value: date | str | None) -> str:
    """Месяц словами: «октябрь 2026»."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return f"{MONTHS_NOM[value.month - 1]} {value.year}"

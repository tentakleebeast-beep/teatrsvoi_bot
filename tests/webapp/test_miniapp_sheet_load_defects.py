"""Репро-тесты дефектов Э5, найденных на ревью t_47444202 (docs/development/149).

Карта t_567658a0: проверка по риску. Каждый тест падает на коде до правки
(53c3a35) и проходит после неё (a7dc1dc / merge e8127c2):

* К2 — настоящий сервер кладёт совпавшие дни в `days`; экран обязан пропустить
  их в списке и посчитать один раз. Здесь ответ подменён через `page.route`
  формой настоящего ответа, чтобы тест не зависел от фикстур стенда.
* К11 — когда совпало всё, человек видит «загружать нечего», а не пустой
  список с выключенной кнопкой «Выберите дни».
* §6.2 — счётчик печатает «N дней из 7», а не «N из 7».

Запуск:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \
        tests/webapp/test_miniapp_sheet_load_defects.py -q --browser webkit
"""

from __future__ import annotations

import datetime as dt

import pytest

FIXED_TIME = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
WEEKDAY = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _day(offset: int, state: str, *, sheet_text: str, sheet_state: str,
         db_text: str | None = None) -> dict:
    return {
        "day": f"2026-10-{26 + offset:02d}",
        "weekday": WEEKDAY[offset],
        "sheet_text": sheet_text,
        "sheet_state": sheet_state,
        "db_text": db_text,
        "db_state": "busy" if db_text else "empty",
        "state": state,
    }


@pytest.fixture
def week_page(app_page):
    page = app_page("/webapp/week")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)
    page.wait_for_timeout(100)
    return page


def _answer(days: list[dict], counts: dict) -> dict:
    return {
        "ok": True, "start": "2026-10-26", "title": "26 октября — 01 ноября",
        "source": {"spreadsheet": "Расписание студийцы", "tab": "студийцы"},
        "person": {"id": 30, "name": "Тестовый Участник", "column": "K"},
        "days": days, "counts": counts,
    }


def _route(page, payload: dict) -> None:
    page.route("**/webapp/availability/sheet/preview*",
               lambda route: route.fulfill(json=payload))


def _open(page) -> None:
    page.locator("#load-from-sheet").click()
    page.wait_for_selector("#sheet-load-list", timeout=5_000)


def test_к2_совпавшие_дни_в_days_не_дают_строк_и_считаются_один_раз(week_page) -> None:
    """К2 репро: `same` в `days` (форма настоящего ответа) — строк нет, счётчик 6 из 7."""
    page = week_page
    # В листе отмечено 6 клеток: 2 совпали, 1 расходится, 2 новые, 1 пустая в базе.
    payload = _answer([
        _day(0, "differs", sheet_text="свободен", sheet_state="free",
             db_text="занят с 12:00 до 15:00"),
        _day(1, "same", sheet_text="занята до 19:00", sheet_state="busy",
             db_text="занята до 19:00"),
        _day(2, "new", sheet_text="занят весь день", sheet_state="busy"),
        _day(3, "sheet_empty", sheet_text="", sheet_state="empty",
             db_text="занят весь день"),
        _day(4, "same", sheet_text="свободна с 20:00", sheet_state="free",
             db_text="свободна с 20:00"),
        _day(5, "new", sheet_text="свободен", sheet_state="free"),
        _day(6, "unparsed", sheet_text="?", sheet_state="unparsed"),
    ], {"same": 2, "new": 2, "differs": 1, "sheet_empty": 1, "unparsed": 1})
    _route(page, payload)
    _open(page)

    # Совпавшие дни — не строки выбора.
    assert page.locator('.ui-row[data-day="2026-10-27"]').count() == 0
    assert page.locator('.ui-row[data-day="2026-10-30"]').count() == 0
    # Строки: 1 расхождение + 2 новых + 1 пустая (переключатели) + 1 неразобранное.
    assert page.locator(".ui-switch-row").count() == 4
    # Счётчик считает совпавшие один раз и со словом «дней» (§6.2).
    assert page.locator("#sheet-load-note").inner_text() == "Отмечено в листе: 6 дней из 7."
    assert page.locator("#sheet-load-same").inner_text() == "Совпало: 2 дня из 7."


def test_к11_когда_совпало_всё_нет_ни_строк_ни_кнопки(week_page) -> None:
    """К11 репро: полное совпадение — сообщение §7, кнопки нет, выбирать нечего."""
    page = week_page
    days = [
        _day(offset, "same", sheet_text="занят весь день", sheet_state="busy",
             db_text="занят весь день")
        for offset in range(7)
    ]
    _route(page, _answer(days, {"same": 7, "new": 0, "differs": 0,
                                "sheet_empty": 0, "unparsed": 0}))
    page.locator("#load-from-sheet").click()
    page.wait_for_selector("#sheet-load-nothing", timeout=5_000)

    assert page.locator("#sheet-load-nothing").inner_text() == (
        "В листе и в приложении одно и то же — загружать нечего.")
    assert page.locator(".ui-switch-row").count() == 0
    primary = page.locator(".ui-sheet__actions .ui-btn--primary")
    assert primary.is_hidden(), "кнопка «Выберите дни» осталась видимой"
    assert page.locator("#sheet-load-note").inner_text() == "Отмечено в листе: 7 дней из 7."


def test_счётчик_печатает_один_день_из_семи(week_page) -> None:
    """§6.2: единственная отмеченная клетка — «Отмечено в листе: 1 день из 7»."""
    page = week_page
    # Остальные шесть дней пусты и в листе, и в базе — в ответ они не попадают.
    days = [_day(0, "new", sheet_text="свободен", sheet_state="free")]
    _route(page, _answer(days, {"same": 0, "new": 1, "differs": 0,
                                "sheet_empty": 0, "unparsed": 0}))
    _open(page)
    assert page.locator("#sheet-load-note").inner_text() == "Отмечено в листе: 1 день из 7."
    assert page.locator("#sheet-load-same").inner_text() == ""

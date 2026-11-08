"""«Время → Моя неделя»: состояние дней видно, сегодня выделен, действия на экране.

Проверки держат решения владельца V-028 (а, в, д), V-029 (а) и V-030 (а)
(docs/reports/74): состояние дня читается цветом и словом, сегодняшний день
выделен, дублирующий сегмент «Моя неделя | Сводка» убран, «Заполнить по
шаблону» и «Очистить неделю» доступны без прокрутки на 390×844, подпись дня
на сводке — в одну строку, кнопка сводки не уходит под нижнюю панель.

Дату фиксируем часами браузера: стенд отдаёт неделю 26.10–01.11.2026, и
сегодняшний день в ней — воскресенье 1 ноября. Иначе тест зависел бы от дня
прогона.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import datetime as dt

import pytest

VIEWPORT_HEIGHT = 844
FIXED_TIME = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)   # сегодня в фикстуре
TODAY_SLOT = 6                                                         # вс 01.11.2026


@pytest.fixture
def week_page(app_page):
    """Страница недели с остановленными часами: дата одна на всех прогонах."""
    page = app_page("/webapp/week")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)
    return page


def _visible_without_scroll(page, selector: str) -> None:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"нет {selector}"
    tabbar = page.locator(".ui-tabbar").first.bounding_box()
    assert tabbar is not None, "нет нижней панели"
    assert box["y"] + box["height"] <= tabbar["y"], (
        f"{selector}: низ на {box['y'] + box['height']:.0f} px, "
        f"нижняя панель с {tabbar['y']:.0f} px — уходит под прокрутку")


def test_неделя_читается_без_прокрутки(week_page) -> None:
    page = week_page

    # Дублирующего переключателя нет: развилка осталась вкладкой «Время» внизу.
    assert page.locator('[role="tablist"]').count() == 0, "сегмент «Моя неделя | Сводка» вернулся"
    assert page.locator('.ui-tabbar__item[aria-current="page"]').inner_text().strip() == "Время"

    # Вся страница помещается в экран 390×844.
    height = page.evaluate("document.documentElement.scrollHeight")
    assert height <= VIEWPORT_HEIGHT, f"страница {height} px при экране {VIEWPORT_HEIGHT} px"

    # Обе основные кнопки недели видны до прокрутки.
    _visible_without_scroll(page, "#fill-template")
    _visible_without_scroll(page, "#clear-week")
    _visible_without_scroll(page, "#mark-today")


def test_состояние_дня_видно_цветом_и_словом(week_page) -> None:
    page = week_page

    states = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => row.dataset.state)")
    assert states == ["busy", "busy", "empty", "free", "busy", "empty", "empty"], states

    words = page.eval_on_selector_all(
        "[data-day-slot] .ui-day__state", "els => els.map(el => el.textContent.trim())")
    assert words == ["занят", "занят", "не отмечено", "свободен", "занят",
                     "не отмечено", "не отмечено"], words

    # Цвет фона у трёх состояний разный: состояние видно до чтения подписи.
    colors = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => getComputedStyle(row).backgroundColor)")
    assert len({colors[0], colors[3], colors[2]}) == 3, colors

    # Полоса слева тоже отличается, а не только слово.
    bars = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => getComputedStyle(row, '::before').backgroundColor)")
    assert len({bars[0], bars[3], bars[2]}) == 3, bars


def test_сегодняшний_день_выделен(week_page) -> None:
    page = week_page

    today = page.locator(".ui-row--today")
    assert today.count() == 1, "выделен не ровно один день"
    assert today.get_attribute("data-day-slot") == str(TODAY_SLOT)
    assert today.locator(".ui-day__today").is_visible(), "у сегодняшнего дня нет слова «сегодня»"
    assert "сегодня" in today.inner_text()

    # Рамка выделения действительно отрисована.
    shadow = today.evaluate("el => getComputedStyle(el).boxShadow")
    assert shadow and shadow != "none", shadow


def test_сводка_в_одну_строку_и_кнопка_видна(app_page) -> None:
    page = app_page("/webapp/week/summary")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"] .ui-day__state').textContent"
        " && document.querySelector('[data-day-slot=\"0\"] .ui-day__state').textContent !== '…'",
        timeout=10_000)
    page.wait_for_timeout(400)

    assert page.locator('[role="tablist"]').count() == 0, "сегмент «Моя неделя | Сводка» вернулся"
    # Адрес входа несёт даты показанной недели (Ш-05), поэтому сравниваем префикс.
    _visible_without_scroll(page, 'a#to-slots')
    assert page.get_attribute("a#to-slots", "href").startswith("/webapp/slots?from=")

    # Подпись дня укладывается в одну строку, а не растягивает строку на две.
    single = page.eval_on_selector_all(
        "[data-day-slot] .ui-row__meta",
        "els => els.map(el => ({white: getComputedStyle(el).whiteSpace,"
        " h: el.getBoundingClientRect().height,"
        " line: parseFloat(getComputedStyle(el).lineHeight)}))")
    for item in single:
        assert item["white"] == "nowrap", item
        assert item["h"] <= item["line"] + 1.5, item

    height = page.evaluate("document.documentElement.scrollHeight")
    assert height <= VIEWPORT_HEIGHT, f"страница сводки {height} px при экране {VIEWPORT_HEIGHT} px"

    # Сегодняшний день выделен и здесь.
    assert page.locator(".ui-row--today").count() == 1

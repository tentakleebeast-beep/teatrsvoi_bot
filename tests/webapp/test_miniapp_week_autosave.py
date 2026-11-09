"""Занятость в Mini App: автосохранение листа дня и шаблон отдельной кнопкой.

Проверяем решения П-4 и З1/З5/З8 (docs/reports/124 §3, план 126 Ш-03):

* отметки дня уходят на сервер при закрытии листа — кнопки «Сохранить» нет;
* лист, открытый и закрытый без правок, ничего не пишет;
* переключение недели не сбрасывает несохранённое: запрос записи уходит
  раньше запроса новой недели;
* «Вернуть» живёт 10 секунд — единственный откат автосохранения;
* «Сохранить шаблон недели» — отдельная кнопка (З8), «Заполнить по шаблону»
  открывает лист предпросмотра (З5).

Обоснование: FR-C9, FR-C10, FR-H8; docs/reports/124 §3 (З1/З5/З8), §5.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.webapp.server import WEEK_EVENTS

FIXED_TIME = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
EMPTY_DAY = 2            # ср 28.10.2026 — день без отметки на стенде
NEXT_WEEK = "2026-11-02"


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    """Журнал стенда — на каждый тест свой: иначе порядок запросов не проверить."""
    WEEK_EVENTS.clear()


@pytest.fixture
def week_page(app_page):
    """Страница недели с остановленными часами и поднятой неделей стенда."""
    page = app_page("/webapp/week")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)
    page.wait_for_timeout(100)
    return page


def _open_day(page, slot: int = EMPTY_DAY) -> None:
    page.locator(f'[data-day-slot="{slot}"]').click()
    page.wait_for_selector(".ui-sheet__actions .ui-btn", timeout=5_000)


def _choose_free(page) -> None:
    page.locator('#day-modes .ui-radio-row[data-mode="free"]').click()


def _close_outside(page) -> None:
    """Закрытие листа мимо кнопки: касание затемнения над листом."""
    page.mouse.click(10, 10)
    page.wait_for_selector(".ui-sheet", state="detached", timeout=5_000)


def _posts() -> list[dict]:
    return [body for kind, body in WEEK_EVENTS if kind == "POST"]


def test_закрытие_листа_сохраняет_без_кнопки(week_page) -> None:
    page = week_page
    assert _posts() == []

    _open_day(page)
    _choose_free(page)
    _close_outside(page)

    page.wait_for_timeout(300)
    posts = _posts()
    assert posts, "закрытие листа не записало отметку — автосохранения нет"
    days = {item["day"]: item for item in posts[-1]["days"]}
    assert days["2026-10-28"]["status"] == "free", days


def test_готово_сохраняет_тем_же_путём(week_page) -> None:
    page = week_page
    _open_day(page)
    _choose_free(page)
    page.locator(".ui-sheet__actions .ui-btn", has_text="Готово").click()

    page.wait_for_timeout(300)
    posts = _posts()
    assert posts, "кнопка «Готово» ничего не записала"
    assert {item["day"]: item for item in posts[-1]["days"]}["2026-10-28"]["status"] == "free"


def test_без_правок_лист_ничего_не_пишет(week_page) -> None:
    page = week_page
    _open_day(page)
    _close_outside(page)

    page.wait_for_timeout(300)
    assert _posts() == [], "лист без правок записал отметки"


def test_смена_недели_не_сбрасывает_несохранённое(week_page) -> None:
    page = week_page
    _open_day(page)
    _choose_free(page)
    _close_outside(page)
    page.locator("#next-week").click()

    page.wait_for_timeout(500)
    kinds = [kind for kind, _ in WEEK_EVENTS]
    assert "POST" in kinds, "перед сменой недели отметка не записалась"
    following = [body.get("start") for kind, body in WEEK_EVENTS
                 if kind == "GET" and body.get("start") == NEXT_WEEK]
    assert following, "новая неделя не запрашивалась"
    index_post = kinds.index("POST")
    index_next = next(number for number, (kind, body) in enumerate(WEEK_EVENTS)
                      if kind == "GET" and body.get("start") == NEXT_WEEK)
    assert index_post < index_next, "неделя переключилась раньше записи — данные могли потеряться"


def test_вернуть_живёт_десять_секунд(week_page) -> None:
    page = week_page
    _open_day(page)
    _choose_free(page)
    _close_outside(page)

    page.wait_for_selector(".ui-toast__action", timeout=5_000)
    assert page.locator(".ui-toast__action").inner_text().strip() == "Вернуть"

    page.clock.fast_forward(9_000)
    assert page.locator(".ui-toast").count() == 1, "«Вернуть» исчез раньше десяти секунд"

    page.clock.fast_forward(2_000)
    assert page.locator(".ui-toast").count() == 0, "«Вернуть» остался дольше десяти секунд"


def test_шаблон_сохраняется_отдельной_строкой(week_page) -> None:
    """Ответ V-048: шаблон переехал из кнопок в нижний блок — строка с пояснением."""
    page = week_page
    row = page.locator("#save-template")
    assert row.locator(".ui-row__title").inner_text().strip() == "Сохранить шаблон недели"
    assert row.locator(".ui-row__meta").inner_text().strip() == "запомнить эту неделю как обычную"
    overflow = row.evaluate("el => el.scrollWidth - el.clientWidth")
    assert overflow <= 1, f"подпись строки не помещается: +{overflow} px"
    row.click()

    page.wait_for_timeout(300)
    saved = [body for kind, body in WEEK_EVENTS if kind == "TEMPLATE_FROM_WEEK"]
    assert saved and saved[0]["start"] == "2026-10-26", saved


def test_заполнить_по_шаблону_показывает_предпросмотр(week_page) -> None:
    page = week_page
    page.locator("#fill-template").click()

    page.wait_for_selector(".ui-sheet__title", timeout=5_000)
    assert "обычной неделе" in page.locator(".ui-sheet__title").inner_text()
    rows = page.locator("#tpl-list .ui-row").count()
    assert rows == 2, "в предпросмотре не видно дней шаблона"
    previews = [body for kind, body in WEEK_EVENTS if kind == "TEMPLATE_APPLY"]
    assert previews and previews[0]["apply"] is False, previews

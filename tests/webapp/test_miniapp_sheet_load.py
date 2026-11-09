"""«Загрузить из общего листа» на неделе: предпросмотр, выбор дней, откат.

Э5 по docs/development/149 §6.2–6.3 и критериям К1–К14: строка открывает лист
без единого POST, состояния дня приходят с сервера (новый включён, расхождение
и пустая клетка выключены, неразобранное не выбирается), основная кнопка
считает выбранное, загрузка отправляет ровно выбранные даты, «Вернуть»
возвращает прежние отметки.

Стенд отдаёт неделю 26.10–01.11.2026 и фиксированный предпросмотр:
новые 28 и 31, расхождение 26, пустая клетка 29, не разобрано 01, совпали 27 и 30.

Обоснование: FR-C9, FR-C10, docs/development/149 §6.2-6.3, docs/reports/74 V-048.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.webapp.server import SHEET_LOADED, WEEK_EVENTS

FIXED_TIME = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
NEW_DAYS = ["2026-10-28", "2026-10-31"]


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    """Журнал стенда и «загруженные» дни — на каждый тест свои."""
    WEEK_EVENTS.clear()
    SHEET_LOADED.clear()


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


def _open(page) -> None:
    page.locator("#load-from-sheet").click()
    page.wait_for_selector("#sheet-load-list .ui-row", timeout=5_000)


def _primary(page):
    return page.locator(".ui-sheet__actions .ui-btn--primary")


def _posts(page) -> list:
    return [entry for entry in WEEK_EVENTS if entry[0] == "SHEET_LOAD"]


def test_строка_открывает_лист_без_записи(week_page) -> None:
    page = week_page
    seen: list[tuple[str, str]] = []
    page.on("request", lambda request: seen.append((request.method, request.url)))

    box = page.locator("#load-from-sheet").bounding_box()
    tabbar = page.locator(".ui-tabbar").bounding_box()
    assert box is not None and tabbar is not None
    assert box["y"] + box["height"] <= tabbar["y"], "строка уходит под нижнюю панель"

    _open(page)
    assert "Загрузить из общего листа" in page.locator(".ui-sheet__title").inner_text()
    assert "ваша колонка K" in page.locator("#sheet-load-intro").inner_text()
    assert "Ничего не изменится" in page.locator("#sheet-load-intro").inner_text()
    assert "Отмечено в листе: 6 из 7." == page.locator("#sheet-load-note").inner_text()
    assert "Совпало: 2 дня из 7." == page.locator("#sheet-load-same").inner_text()

    assert not [item for item in seen if item[0] == "POST"], seen
    assert not _posts(page), "предпросмотр не пишет в базу"


def test_состояния_дня_и_переключатели(week_page) -> None:
    page = week_page
    _open(page)

    # Совпавшие дни строк не занимают: 4 переключателя и одна строка «не разобрано».
    assert page.locator(".ui-switch-row").count() == 4
    assert page.locator(".ui-switch-row[data-day=\"2026-10-27\"]").count() == 0
    assert page.locator(".ui-switch-row[data-day=\"2026-10-30\"]").count() == 0

    for day in NEW_DAYS:
        row = page.locator(f'.ui-switch-row[data-day="{day}"]')
        assert row.get_attribute("aria-pressed") == "true", day
        assert row.locator(".ui-switch-row__state").inner_text() == "загрузить"

    differs = page.locator('.ui-switch-row[data-day="2026-10-26"]')
    assert differs.get_attribute("aria-pressed") == "false"
    assert differs.locator(".ui-switch-row__state").inner_text() == "не трогать"
    assert "в листе «свободен»" in differs.inner_text()
    assert "у вас «занят с 12:00 до 15:00»" in differs.inner_text()

    empty = page.locator('.ui-switch-row[data-day="2026-10-29"]')
    assert empty.get_attribute("aria-pressed") == "false"
    assert "в листе пусто" in empty.inner_text()

    unparsed = page.locator('.ui-row[data-day="2026-11-01"]')
    assert "не разобрано, загрузить нельзя" in unparsed.inner_text()
    assert unparsed.locator(".ui-switch").count() == 0
    assert "?»" in unparsed.inner_text()


def test_кнопка_считает_выбор(week_page) -> None:
    page = week_page
    _open(page)
    primary = _primary(page)
    assert primary.inner_text() == "Загрузить 2 дня"
    assert primary.is_enabled()

    page.locator('.ui-switch-row[data-day="2026-10-26"]').click()
    assert primary.inner_text() == "Загрузить 3 дня"

    for day in NEW_DAYS:
        page.locator(f'.ui-switch-row[data-day="{day}"]').click()
    assert primary.inner_text() == "Загрузить 1 день"

    page.locator('.ui-switch-row[data-day="2026-10-26"]').click()
    assert primary.inner_text() == "Выберите дни"
    assert primary.is_disabled()

    page.locator('.ui-switch-row[data-day="2026-10-31"]').click()
    assert primary.inner_text() == "Загрузить 1 день"


def test_загрузка_отправляет_только_выбранные_дни(week_page) -> None:
    page = week_page
    _open(page)
    _primary(page).click()
    page.wait_for_selector(".ui-toast", timeout=5_000)

    posts = _posts(page)
    assert len(posts) == 1, posts
    assert posts[0][1] == {"start": "2026-10-26", "days": NEW_DAYS}, posts[0][1]
    assert page.locator(".ui-sheet").count() == 0, "лист не закрылся после загрузки"

    toast = page.locator(".ui-toast")
    assert toast.locator(".ui-toast__text").inner_text() == "Загружено: 2 дня"
    assert toast.locator(".ui-toast__action").inner_text() == "Вернуть"

    # Неделя перечитана и показывает загруженные дни отмеченными.
    assert page.locator('[data-day-slot="2"]').get_attribute("data-state") == "busy"
    assert page.locator('[data-day-slot="5"]').get_attribute("data-state") == "busy"


def test_вернуть_возвращает_прежние_отметки(week_page) -> None:
    page = week_page
    _open(page)
    _primary(page).click()
    page.wait_for_selector(".ui-toast__action", timeout=5_000)

    page.locator(".ui-toast__action").click()
    page.wait_for_function(
        "() => { const t = document.querySelector('.ui-toast__text');"
        " return t && t.textContent === 'Вернул как было.'; }",
        timeout=5_000)

    kinds = [entry[0] for entry in WEEK_EVENTS]
    # Отмеченные загрузкой дни в снимке отсутствовали — сначала очистка, потом запись.
    assert "CLEAR" in kinds, kinds
    assert kinds.index("CLEAR") < kinds.index("POST"), kinds
    assert page.locator('[data-day-slot="2"]').get_attribute("data-state") == "empty"

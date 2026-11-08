"""Сетка месяца «люди × дни»: слова состояний, расхождения, неживая клетка (FR-H9, З9).

Обоснование: FR-H9 (сводка «люди × дни» — только чтение, только организатору),
план 126 Ш-04, docs/reports/124 §3 (З9) и docs/development/48 §3. Дефект, который
сторожит тест: клетка сетки была кнопкой и перебирала состояния — месяц оставался
вторым рабочим местом (П-3), а правка дня должна жить в листе дня (З1).

Роль на стенде задаётся заголовком `X-SVOI-Role` (tests/webapp/server.py):
`participant` — без `schedule.read.all`, иначе организатор.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import pytest

PARTICIPANT = {"X-SVOI-Role": "participant"}


def _loaded(page) -> None:
    """Ждём, пока сетка нарисовала хотя бы одну неделю (стенд отдаёт ноябрь 2026)."""
    page.wait_for_function(
        "() => document.querySelectorAll('.ui-mcell[data-state]').length > 0",
        timeout=10_000)


def _show_day(page, day: str):
    """Прокрутить недели до дня с состоянием: месяц листается, а не скроллится."""
    previous = page.locator("#prev-week")
    for _ in range(6):
        if previous.is_disabled():
            break
        previous.click()
    for _ in range(6):
        found = page.locator(f'.ui-mcell[data-day="{day}"][data-state]')
        if found.count():
            return found.first
        following = page.locator("#next-week")
        if following.is_disabled():
            break
        following.click()
    pytest.fail(f"день {day} не показался ни на одной неделе месяца")


def test_организатор_видит_месяц_словами(app_page) -> None:
    """Люди в строках, дни в колонках, состояние — словом, расхождение — списком."""
    page = app_page("/webapp/week/month")
    _loaded(page)

    rows = page.locator(".ui-month__row[data-person]")
    assert rows.count() == 2, "в сетке не по строке на человека"
    assert "Тестовый Участник" in page.locator(".ui-month__who").first.inner_text()
    assert page.locator(".ui-month__day").count() == 7, "в шапке не семь дней недели"

    busy = _show_day(page, "2026-11-03")
    assert busy.get_attribute("data-state") == "busy"
    assert busy.inner_text().strip() == "занят", "состояние не словом"

    screen = page.locator(".ui-screen").inner_text()
    assert "Расхождения с базой" in screen
    assert "в базе" in page.locator("#diffs-list").inner_text()
    assert not page.svoi_errors, page.svoi_errors


def test_клетка_не_реагирует_на_нажатие(app_page) -> None:
    """П-3: месяц только читается — нажатие на клетку не шлёт запрос и ничего не меняет."""
    page = app_page("/webapp/week/month")
    _loaded(page)
    cell = _show_day(page, "2026-11-03")

    tag = cell.evaluate("el => el.tagName.toLowerCase()")
    assert tag == "div", f"клетка — цель нажатия ({tag}), а месяц только для глаз"
    inside_target = cell.evaluate(
        "el => !!(el.closest('button, a, input, select, textarea'))")
    assert not inside_target, "клетка внутри кнопки или ссылки"

    before = cell.inner_text()
    url = page.url
    requests: list[tuple[str, str]] = []
    page.on("request", lambda request: requests.append((request.method, request.url)))

    cell.click()
    page.wait_for_timeout(400)

    assert not requests, f"нажатие на клетку сходило в сеть: {requests}"
    assert cell.inner_text() == before, "состояние клетки изменилось от нажатия"
    assert page.url == url, "нажатие на клетку увело со экрана"
    assert page.locator(".ui-sheet, .ui-toast").count() == 0, "нажатие открыло лист"
    assert not page.svoi_errors, page.svoi_errors


def test_месяц_ведёт_со_сводки(app_page) -> None:
    """З9: вход в месяц — строка «Сетка месяца» на сводке недели."""
    page = app_page("/webapp/week/summary")
    page.wait_for_selector("#to-month", state="visible", timeout=10_000)

    link = page.locator("#to-month")
    assert link.is_visible(), "со сводки пропал вход в сетку месяца"
    assert (link.get_attribute("href") or "").startswith("/webapp/week/month")


def test_участнику_без_права_месяц_не_показан(app_page) -> None:
    """403 — это не сеть: экран называет право, а не советует «проверить сеть»."""
    page = app_page("/webapp/week/month", headers=PARTICIPANT)
    page.wait_for_function(
        "() => { var s = document.getElementById('status');"
        " return s && s.textContent.trim().length > 0; }",
        timeout=10_000)

    text = page.locator("#status").inner_text().strip()
    assert "у организатора" in text, text
    assert "сеть" not in text.lower(), f"экран врёт про сеть: {text}"
    assert page.locator(".ui-mcell[data-state]").count() == 0, "участник увидел данные"

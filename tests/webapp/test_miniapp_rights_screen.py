"""Права: без `participants.manage` матрицы и формы выдачи нет, вместо них — чьё это право.

Отчёт 142 §3 строка 25, принципы 8 и Б3 из docs/development/91, карта t_eb9e0661.
Раздел `rights` ответа `/webapp/admin/overview` отдаётся только с возможностью
`participants.manage` (`app/webapp.py:ADMIN_BLOCKS`): у администратора она есть, у
организатора, педагога и труппы — нет (`db/init/008`, `db/init/061`). Прежний экран
получал пустой список `matrix` и печатал «Матрица пуста. Роли не настроены» — ложное
утверждение о данных (у администратора матрица полная) — рядом с формой «Выдать право
человеку», которой человек без права всё равно не мог воспользоваться.

Теперь без права экран не рисует ни матрицу, ни форму поиска и говорит «Права выдаёт
администратор»; с правом поведение прежнее.

Обоснование: FR-A6 (матрица роль → возможности), docs/reports/142 §3 строка 25.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_rights_screen.py \\
        -q --browser chromium
"""

from __future__ import annotations

DENIED = "Раздел доступен организатору и администратору."
NO_RIGHT = "Права выдаёт администратор"
SCREEN = "/webapp/admin/t/rights"

ORG = {"X-SVOI-Role": "org"}
MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


def _no_matrix_no_form(page, who: str) -> None:
    """Ни матрицы, ни формы выдачи: разделов нет в разметке на экране, карточек — ноль."""
    assert not page.locator("#rights-matrix").is_visible(), f"у {who} снова видна матрица ролей"
    assert not page.locator("#rights-grant").is_visible(), f"у {who} снова видна форма выдачи"
    assert page.locator("#matrix .ui-card").count() == 0, f"у {who} матрица всё-таки нарисована"
    assert page.locator("#rights-results .ui-row").count() == 0, \
        f"у {who} нарисованы строки выдачи"
    text = _screen_text(page)
    assert "Матрица пуста" not in text, f"ложное утверждение о данных: {text}"
    assert "Выдать право человеку" not in text, f"у {who} заголовок формы выдачи: {text}"


def test_org_без_права_не_видит_ни_матрицы_ни_формы(app_page) -> None:
    """`participants.manage` у org нет (`db/init/008`): матрицы и формы выдачи быть не может."""
    page = app_page(SCREEN, headers=ORG)
    _settled(page)

    _no_matrix_no_form(page, "org")
    text = _screen_text(page)
    assert NO_RIGHT in text, text
    assert page.locator("#status").inner_text().strip() == "", \
        "у org панель открыта — отказа в статусе быть не должно"
    assert page.svoi_errors == [], page.svoi_errors


def test_master_без_права_не_видит_ни_матрицы_ни_формы(app_page) -> None:
    """У педагога панель есть (люди), а `participants.manage` нет (`db/init/061`)."""
    page = app_page(SCREEN, headers=MASTER)
    _settled(page)

    _no_matrix_no_form(page, "master")
    assert NO_RIGHT in _screen_text(page)
    assert page.svoi_errors == [], page.svoi_errors


def test_troupe_не_видит_ни_матрицы_ни_формы(app_page) -> None:
    """У труппы панель закрыта (403): причину печатает отказ, разделов нет."""
    page = app_page(SCREEN, headers=TROUPE)
    _settled(page)

    _no_matrix_no_form(page, "troupe")
    assert NO_RIGHT not in _screen_text(page), "403 панели: разделов нет, причину печатает отказ"
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors


def test_admin_видит_матрицу_и_форму(app_page) -> None:
    """У администратора право есть: матрица на месте, поведение прежнее."""
    page = app_page(SCREEN)
    _settled(page)

    assert page.locator("#rights-matrix").is_visible(), "у администратора пропала матрица"
    assert page.locator("#matrix .ui-card").count() == 4, "матрица ролей нарисована не целиком"
    assert page.locator("#rights-grant").is_visible(), "у администратора пропала форма выдачи"
    assert page.locator("#rights-search").is_visible()
    text = _screen_text(page)
    assert "Роли и возможности" in text and "Выдать право человеку" in text
    assert "Матрица пуста" not in text, "у администратора матрица не пуста"
    assert NO_RIGHT not in text
    assert page.locator("#status").inner_text().strip() == ""
    assert page.svoi_errors == [], page.svoi_errors

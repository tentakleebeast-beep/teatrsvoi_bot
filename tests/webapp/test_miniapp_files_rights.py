"""«Файлы студии» в Mini App: одно право `files.access` и три роли (V-039 (в), Б3).

Обоснование: V-039 (в) `docs/reports/74-questions.md` (решение владельца 08.11.2026:
раздел видят admin, org и master), docs/reports/142 §3 строка 16, Б3 —
состояние «нет права» передаётся словами, а не пустым списком.

Дефект, который сторожат тесты: экран был закрыт `admin.panel`, поэтому у org и master
было «Раздел доступен администратору», хотя право `files.access` у них есть. Вторая
половина той же правки — у студийца права нет (миграция 062), и отказ должен быть
словами, а не тишиной.

Стенд: `tests/webapp/server.py` отвечает 403 на чтение файлов по заголовку
`X-SVOI-Role: troupe|participant`, как tool-api роли без `files.access`; без заголовка
роль — администратор.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import pytest

# Роли с правом `files.access` (db/init/008, 061): раздел открыт им.
WITH_RIGHT = [
    pytest.param(None, id="admin"),
    pytest.param({"X-SVOI-Role": "org"}, id="org"),
    pytest.param({"X-SVOI-Role": "master"}, id="master"),
]


@pytest.mark.parametrize("headers", WITH_RIGHT)
def test_с_правом_экран_показывает_файлы_и_место(app_page, headers) -> None:
    """admin, org и master видят список файлов и полосу места — без отказа."""
    page = app_page("/webapp/files", headers=headers)
    page.wait_for_selector("#quota-text", timeout=10_000)
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-row').length > 0", timeout=10_000)

    assert page.locator("#list .ui-row").count() >= 1, "список файлов пуст там, где право есть"
    assert "Раздел доступен" not in page.inner_text("body"), "право есть, а экран отказал"
    assert page.locator("#upload").is_enabled() is False  # до выбора файлов кнопка ждёт
    assert not page.svoi_errors, page.svoi_errors


def test_без_права_экран_говорит_у_кого_файлы(app_page) -> None:
    """Студиец читает, кому открыт раздел, — словами, а не пустым списком (Б3)."""
    page = app_page("/webapp/files", headers={"X-SVOI-Role": "troupe"})
    page.wait_for_selector("#status", timeout=10_000)

    text = page.inner_text("body")
    assert "Раздел доступен администратору, организатору и мастеру" in text, text
    assert page.locator("#list .ui-row").count() == 0, "файлы показаны роли без права"
    assert page.locator("#upload").is_disabled(), "кнопка загрузки без права осталась рабочей"


@pytest.mark.parametrize("headers", WITH_RIGHT)
def test_кнопка_удаления_есть_у_роли_с_правом(app_page, headers) -> None:
    """FR-K5 (V-045): у роли с `files.access` в строке файла есть «Удалить».

    Правило владельца — недоступное действие не показываем; поэтому кнопка живёт в том
    же списке, который приходит только с правом, и не появляется отдельным обещанием.
    """
    page = app_page("/webapp/files", headers=headers)
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-row').length > 0", timeout=10_000)

    assert page.locator("#list .ui-row button:has-text('Удалить')").count() >= 1, \
        "в строке файла нет кнопки удаления"
    assert not page.svoi_errors, page.svoi_errors


def test_без_права_кнопки_удаления_нет(app_page) -> None:
    """Роль без `files.access` не видит и кнопки удаления: файлов на экране нет вовсе."""
    page = app_page("/webapp/files", headers={"X-SVOI-Role": "troupe"})
    page.wait_for_selector("#status", timeout=10_000)

    assert page.locator("button:has-text('Удалить')").count() == 0


def test_кнопка_удаления_спрашивает_подтверждение(app_page) -> None:
    """FR-L8: удаление не срабатывает первым нажатием — открывается лист с вопросом."""
    page = app_page("/webapp/files")
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-row').length > 0", timeout=10_000)

    page.locator("#list .ui-row button:has-text('Удалить')").first.click()
    page.wait_for_selector(".ui-sheet", timeout=5_000)
    text = page.inner_text(".ui-sheet")
    assert "Удалить файл?" in text, text
    assert "Отмена" in text and "Удалить" in text
    # До подтверждения файл остаётся в списке.
    assert page.locator("#list .ui-row").count() >= 1
    assert not page.svoi_errors, page.svoi_errors

"""Экран «Ещё → Заметки»: список, лист заметки и запись (FR-F7).

Проверяется то, что видит человек, а не разметка: заметки приходят списком, личная
и общая различимы, лист открывается с полным текстом, чужая общая не даёт кнопки
«Изменить», а новая заметка сохраняется и подтверждается сообщением.

Обоснование: FR-F7 (docs/development/19), docs/development/92 §2 «Личное».
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("device", ("Pixel 7", "Galaxy S9+"))
def test_экран_заметок_без_js_ошибок(device_page, device):
    page = device_page("/webapp/notes", device)
    page.wait_for_timeout(700)

    assert page.svoi_errors == [], page.svoi_errors
    assert "Записать заметку?" not in page.inner_text("body")
    titles = [row.inner_text().strip() for row in page.locator("#list .ui-row__title").all()]
    assert titles == ["Взять реквизит", "Зал на Обороны"], titles
    assert page.locator("#count").inner_text().startswith("2"), page.inner_text("#count")

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{device}: экран заметок шире экрана"


def test_лист_своей_заметки_показывает_текст_и_правится(device_page):
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)

    page.locator("#list .ui-row", has_text="Взять реквизит").click()
    sheet = page.locator(".ui-sheet")
    assert sheet.count() == 1, "лист заметки не открылся"
    assert "Взять реквизит" in sheet.inner_text()
    assert "Спросить у Иры" in sheet.inner_text(), "текст заметки обрезан"

    buttons = [button.inner_text().strip() for button in
               page.locator(".ui-sheet__actions button").all()]
    assert "Изменить" in buttons, buttons

    page.locator(".ui-sheet__actions button", has_text="Изменить").click()
    page.wait_for_timeout(300)
    assert page.input_value("#body").startswith("Взять реквизит из склада"), \
        page.input_value("#body")
    assert page.inner_text("#form-title") == "Правка заметки"
    assert page.svoi_errors == [], page.svoi_errors


def test_чужая_общая_заметка_без_кнопки_изменить(device_page):
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)

    page.locator("#list .ui-row", has_text="Зал на Обороны").click()
    sheet = page.locator(".ui-sheet")
    assert sheet.count() == 1
    assert "автор: Иванов Виктор" in sheet.inner_text()
    buttons = [button.inner_text().strip() for button in
               page.locator(".ui-sheet__actions button").all()]
    assert "Изменить" not in buttons, f"чужую общую нельзя править: {buttons}"
    assert buttons == ["Закрыть"], buttons


def test_новая_заметка_сохраняется(device_page):
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)

    page.fill("#title", "Позвонить в зал")
    page.fill("#body", "Спросить про аренду на декабрь")
    page.fill("#tags", "зал, аренда")
    page.locator('#scope .ui-segmented__item[data-scope="shared"]').click()
    page.click("#save")
    page.wait_for_timeout(700)

    toast = page.locator(".ui-toast")
    assert toast.count() == 1, ("нет подтверждения сохранения; ошибка формы: "
                                + page.inner_text("#error") + "; статус: "
                                + page.inner_text("#status"))
    assert "Заметка сохранена" in toast.inner_text(), toast.inner_text()
    # после сохранения форма снова пустая и снова «Новая заметка»
    assert page.input_value("#body") == ""
    assert page.inner_text("#form-title") == "Новая заметка"
    assert page.svoi_errors == [], page.svoi_errors


def test_пустой_текст_не_сохраняется(device_page):
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)

    page.click("#save")
    page.wait_for_timeout(300)

    error = page.locator("#error")
    assert error.is_visible(), "нет объяснения, почему не сохранилось"
    assert "Напишите текст" in error.inner_text()
    assert page.locator(".ui-toast").count() == 0

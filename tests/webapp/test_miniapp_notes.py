"""Экран «Ещё → Заметки»: список, лист заметки, запись и удаление (FR-F7).

Проверяется то, что видит человек, а не разметка: заметки приходят списком, личная
и общая различимы, лист открывается с полным текстом, чужая общая не даёт кнопок
«Изменить» и «Удалить», а новая заметка сохраняется и подтверждается сообщением.
Удаление спрашивает подтверждение, убирает строку из списка и очищает форму.

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


def test_своя_заметка_удаляется_после_подтверждения(device_page):
    """Удаление: кнопка в листе, второй вопрос, строка исчезает, форма чистая."""
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)
    page.fill("#title", "Черновик")
    page.fill("#body", "черновик, который уберётся после удаления")

    page.locator("#list .ui-row", has_text="Взять реквизит").click()
    buttons = [button.inner_text().strip() for button in
               page.locator(".ui-sheet__actions button").all()]
    assert "Удалить" in buttons, f"в листе своей заметки нет кнопки удаления: {buttons}"

    # Первый вопрос — «Удалить», второй — подтверждение. Отмена ничего не удаляет.
    page.locator(".ui-sheet__actions button", has_text="Удалить").click()
    page.wait_for_timeout(200)
    confirm = page.locator(".ui-sheet")
    assert "Удалить заметку?" in confirm.inner_text(), confirm.inner_text()
    page.locator(".ui-sheet__actions button", has_text="Отмена").click()
    page.wait_for_timeout(200)
    assert page.locator(".ui-sheet").count() == 0, "подтверждение не закрылось"
    titles = [row.inner_text().strip() for row in page.locator("#list .ui-row__title").all()]
    assert "Взять реквизит" in titles, titles

    # Теперь удаляем по-настоящему.
    page.locator("#list .ui-row", has_text="Взять реквизит").click()
    page.locator(".ui-sheet__actions button", has_text="Удалить").click()
    page.wait_for_timeout(200)
    page.locator(".ui-sheet__actions button", has_text="Удалить").click()
    page.wait_for_timeout(800)

    toast = page.locator(".ui-toast")
    assert toast.count() == 1, ("нет подтверждения удаления; ошибка формы: "
                                + page.inner_text("#error") + "; статус: "
                                + page.inner_text("#status"))
    assert "Заметка удалена" in toast.inner_text(), toast.inner_text()
    titles = [row.inner_text().strip() for row in page.locator("#list .ui-row__title").all()]
    assert "Взять реквизит" not in titles, titles
    assert titles == ["Зал на Обороны"], titles
    assert page.input_value("#body") == "", "форма держит текст удалённой заметки"
    assert page.inner_text("#form-title") == "Новая заметка"
    assert page.svoi_errors == [], page.svoi_errors


def test_чужую_общую_заметку_нельзя_удалить(device_page):
    """Чужой общей заметки нет в правах: ни «Изменить», ни «Удалить»."""
    page = device_page("/webapp/notes", "Pixel 7")
    page.wait_for_timeout(700)

    page.locator("#list .ui-row", has_text="Зал на Обороны").click()
    buttons = [button.inner_text().strip() for button in
               page.locator(".ui-sheet__actions button").all()]
    assert "Удалить" not in buttons, f"чужую общую нельзя удалять: {buttons}"
    assert buttons == ["Закрыть"], buttons

"""Загрузка в Mini App: место, папка и описание (E4, V-124, docs/development/192 §8.1).

До E4 загрузка не спрашивала папку и клала всё в `Личное/<вид>`, а описания не было
вовсе. Здесь проверяется живьём — в настоящем HTML/CSS/JS проекта через стенд
`tests/webapp/server.py`:

* переключатель «В студию / Ссылка на Drive» переключает панели, а не только красит кнопку;
* первый пункт списка папок — «Личное — по виду файла» (пустое значение, FR-K11),
  ниже общие папки путями и свои личные;
* поле «Что это» с подсказкой про поиск; пустое описание допустимо;
* выбранная общая папка показывает «В общую папку: личное место не расходуется.»;
* на 320 px нет горизонтальной прокрутки и нет ошибок страницы.

Обоснование: FR-K1, FR-K3, V-124, docs/development/192 §8.1, §8.7.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations


def _folders_loaded(page) -> None:
    """Ждёт, пока список папок наполнится: первый пункт есть всегда, остальные — с сервера."""
    page.wait_for_function(
        "() => document.querySelectorAll('#folder-pick option').length > 1", timeout=10_000)


def _options(page) -> list[str]:
    return page.eval_on_selector_all(
        "#folder-pick option", "nodes => nodes.map(node => node.textContent.trim())")


def test_переключатель_места_меняет_панель(app_page) -> None:
    """V-124/§8.1: «Ссылка на Drive» скрывает выбор файлов и показывает форму ссылки."""
    page = app_page("/webapp/files")
    _folders_loaded(page)
    assert page.locator("#upload-panel").is_visible() is True
    assert page.locator("#link-panel").is_visible() is False

    page.locator("#place-drive").click()
    page.wait_for_function("() => document.querySelector('#upload-panel').hidden", timeout=5_000)
    assert page.locator("#link-panel").is_visible() is True
    assert page.locator("#place-drive").get_attribute("aria-pressed") == "true"
    assert page.locator("#file").is_hidden()
    assert not page.svoi_errors, page.svoi_errors

    page.locator("#place-studio").click()
    page.wait_for_function("() => !document.querySelector('#upload-panel').hidden", timeout=5_000)
    assert page.locator("#link-panel").is_visible() is False


def test_список_папок_начинается_с_личного_и_знает_оба_типа(app_page) -> None:
    """FR-K11/§8.1: первый пункт — «Личное — по виду файла», дальше общие и свои пути."""
    page = app_page("/webapp/files")
    _folders_loaded(page)
    options = _options(page)
    assert options[0] == "Личное — по виду файла", options
    assert "Спектакли" in options, options
    assert "Мои заметки" in options, options


def test_общая_папка_говорит_что_личное_место_не_расходуется(app_page) -> None:
    """§8.1, §8.7: подсказка появляется только для общей папки (V-125)."""
    page = app_page("/webapp/files")
    _folders_loaded(page)
    assert page.locator("#shared-note").is_visible() is False

    page.select_option("#folder-pick", label="Спектакли")
    page.wait_for_function("() => !document.querySelector('#shared-note').hidden", timeout=5_000)
    assert "личное место не расходуется" in page.inner_text("#shared-note")

    page.select_option("#folder-pick", label="Мои заметки")
    page.wait_for_function("() => document.querySelector('#shared-note').hidden", timeout=5_000)


def test_поле_описания_с_подсказкой_и_пустое_допустимо(app_page) -> None:
    """V-124: поле «Что это» есть, подсказка честно говорит про поиск, пусто — можно."""
    page = app_page("/webapp/files")
    _folders_loaded(page)
    assert page.locator("#describe").is_visible() is True
    hint = page.inner_text(".ui-field:has(#describe) .ui-field__hint")
    assert "Поиск по описанию не идёт" in hint, hint
    assert page.eval_on_selector("#describe", "node => node.value") == ""

    page.fill("#describe", "Смета на реквизит")
    assert page.eval_on_selector("#describe", "node => node.value") == "Смета на реквизит"
    page.fill("#describe", "")
    assert not page.svoi_errors, page.svoi_errors


def test_узкий_экран_без_горизонтальной_прокрутки(app_page) -> None:
    """§8.1: 320 px — самая узкая ширина; поле папки и описание не вылезают за экран."""
    page = app_page("/webapp/files")
    _folders_loaded(page)
    page.set_viewport_size({"width": 320, "height": 640})
    page.wait_for_timeout(200)
    metrics = page.evaluate(
        "() => ({scroll: document.scrollingElement.scrollWidth,"
        " client: document.scrollingElement.clientWidth})")
    assert metrics["scroll"] <= metrics["client"] + 1, metrics
    assert not page.svoi_errors, page.svoi_errors

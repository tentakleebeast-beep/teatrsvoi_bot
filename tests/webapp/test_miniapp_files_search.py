"""Поиск и чипы в Mini App: «где афиша», «что в папке Пурга» (E4, FR-K17).

До E4 поиск шёл только по своим файлам и полнотекстовому индексу: запрос из двух
знаков ничего не находил, хотя имя совпадало. Здесь проверяется живьём — в настоящем
HTML/CSS/JS проекта через стенд `tests/webapp/server.py`:

* ряд чипов ровно тот, что в §8.3: Все · Документы · Текст · Картинки · Аудио · Ссылки;
* чип переводит сетку на `/webapp/files/search`, заголовок становится «Найдено: N»;
* пустой результат говорит словами «Ничего не нашлось», без «возможно, вы имели в виду»;
* плитка ссылки на Drive открывает `open_url` через `tg.openLink` и не уводит страницу;
* подпись «в Drive» стоит вместо размера.

Обоснование: FR-K17, V-123, docs/development/192 §8.3, §8.7.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations


def _rows(page) -> None:
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-grid__cell').length > 0", timeout=10_000)


def _chips(page) -> list[str]:
    return page.eval_on_selector_all(
        "#chips button", "nodes => nodes.map(node => node.textContent.trim())")


def _chip(page, label: str):
    return page.locator("#chips button", has_text=label).first


def _opened(page) -> str:
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    return page.evaluate("() => window.__opened")


def test_ряд_чипов_и_подписи(app_page) -> None:
    """§8.3: шесть чипов, надписи из одной таблицы, а не «слово + ы»."""
    page = app_page("/webapp/files")
    _rows(page)
    assert _chips(page) == ["Все", "Документы", "Текст", "Картинки", "Аудио", "Ссылки"], \
        _chips(page)
    assert not page.svoi_errors, page.svoi_errors


def test_чип_документы_ищет_по_виду_и_подпись_найдено(app_page) -> None:
    """FR-K17: «Документы» находит pdf и не находит заметки .md; заголовок «Найдено: N»."""
    page = app_page("/webapp/files")
    _rows(page)
    _chip(page, "Документы").click()
    page.wait_for_function(
        "() => document.querySelector('#list-title').textContent.indexOf('Найдено:') === 0",
        timeout=10_000)
    names = page.eval_on_selector_all(
        "#list .ui-grid__name", "nodes => nodes.map(node => node.textContent.trim())")
    assert "Смета.pdf" in names, names
    assert "заметки.md" not in names, names


def test_чип_ссылки_и_плитка_открывает_drive(app_page) -> None:
    """V-123/§8.3: «Ссылки» — только drive; плитка открывает `open_url`, страница на месте."""
    page = app_page("/webapp/files")
    _rows(page)
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")

    _chip(page, "Ссылки").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-grid__cell').length === 2", timeout=10_000)
    body = page.inner_text("#list")
    assert "в Drive" in body, body
    assert "Пурга — папка в Drive" in body, body

    before = page.url
    page.locator("#list .ui-grid__tile").first.click()
    opened = _opened(page)
    assert opened.startswith("https://drive.google.com/"), opened
    assert page.url == before, "плитка ушла с экрана вместо открытия ссылки"


def test_пустой_результат_словами(app_page) -> None:
    """§8.3: ничего не нашлось — так и сказано, без догадок о другом запросе."""
    page = app_page("/webapp/files")
    _rows(page)
    page.fill("#q", "нет-такого-файла")
    page.wait_for_function(
        "() => document.querySelector('#list').textContent.indexOf('Ничего не нашлось') >= 0",
        timeout=10_000)
    text = page.inner_text("#list")
    assert "Ищите по имени, папке и типу" in text, text
    assert "возможно, вы имели в виду" not in text.lower(), text
    assert page.inner_text("#list-title") == "Найдено: 0", page.inner_text("#list-title")
    assert not page.svoi_errors, page.svoi_errors

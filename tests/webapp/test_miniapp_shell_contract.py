"""Контракт оболочки: токен и слова входа — из `static/webapp-shell.js`.

Оба дефекта 31.10.2026 статический grep по `function api` не видел:

* `pages/files.py` читал `token` из старого ядра `admin.py`. Ядро разложили,
  имя исчезло, и кнопка скачивания падала с ReferenceError **до** запроса;
* страницы звали `enter()` (boolean) и на отказе во входе печатали своё
  «Нет доступа», теряя причину, которую прислал сервер.

С картой Б2 (V-052/V-053) кнопка — «Скачать» в строке папки: она берёт ссылку
`/webapp/files/{id}/link` и открывает её браузером телефона. Контракт проверяет,
что запрос несёт Bearer оболочки, а отказ читается словами.

Тесты кликают по настоящей кнопке и читают настоящий текст статуса. Включаются
вместе с остальным набором:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations

import json

from tests.webapp.conftest import TELEGRAM_HASH, VIEWPORT


def _tracks(page, count: int = 3) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('#tracks .ui-row').length === n",
        arg=count, timeout=10_000)


def _record_open(page) -> None:
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")


def test_скачивание_ссылки_уходит_с_токеном_оболочки(app_page):
    """«Скачать» в строке шлёт запрос с Bearer оболочки и не падает."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page)
    _record_open(page)

    seen: dict[str, str] = {}

    def handle(route):
        seen["auth"] = route.request.headers.get("authorization", "")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "url": "https://files.example/31",
                                       "expires_in": 900, "name": "01 Увертюра.mp3",
                                       "size_bytes": 1}))

    page.route("**/webapp/files/31/link", handle)
    # Ждём сам запрос: без него нажатие ничего не сделало.
    with page.expect_request("**/webapp/files/31/link", timeout=10_000):
        page.click("button[aria-label='Скачать']")
    page.wait_for_function(
        "() => window.__opened === 'https://files.example/31'", timeout=10_000)

    assert not page.svoi_errors, f"ошибки JS на кнопке скачивания: {page.svoi_errors}"
    assert seen.get("auth"), "запрос ссылки ушёл без Authorization"
    assert seen["auth"] == "Bearer " + page.evaluate("() => window.SVOI.token")


def test_отказ_ссылки_показывает_текст_сервера(app_page):
    """404 на ссылку: человек видит слова о файле, а не общее «не получилось»."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page)
    _record_open(page)

    page.route(
        "**/webapp/files/31/link",
        lambda route: route.fulfill(
            status=404, content_type="application/json",
            body=json.dumps({"ok": False, "code": "file_not_found",
                             "detail": "Файл не найден."})))

    page.click("button[aria-label='Скачать']")
    page.wait_for_function(
        "() => (document.getElementById('status').textContent || '')"
        ".indexOf('Файл не найден') === 0",
        timeout=10_000)

    shown = page.text_content("#status").strip()
    assert shown == "Файл не найден. Возможно, его удалили."
    assert page.evaluate("() => window.__opened") == ""


def test_не_json_отказ_не_ломает_экран(app_page):
    """Если тело не разобрать (шлюз, прокси), экран не падает и не пустеет."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page)
    _record_open(page)

    page.route(
        "**/webapp/files/31/link",
        lambda route: route.fulfill(status=502, content_type="text/html",
                                    body="<h1>502</h1>"))

    page.click("button[aria-label='Скачать']")
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)
    assert page.locator("#tracks .ui-row").count() == 3, "отказ стёр папку"
    assert not page.svoi_errors, page.svoi_errors


def test_отказ_во_входе_показывает_причину_сервера(browser, webapp_server):
    """Экран печатает слова сервера, а не своё «нет доступа»."""
    context = browser.new_context(viewport=VIEWPORT)
    try:
        page = context.new_page()
        page.route(
            "**/webapp/auth",
            lambda route: route.fulfill(
                status=403, content_type="application/json",
                body=json.dumps({"ok": False, "message": "Учётная запись в архиве."})))
        page.goto(f"{webapp_server}/webapp/admin/t/archive{TELEGRAM_HASH}", wait_until="load")
        page.wait_for_function(
            "() => (document.getElementById('status').textContent || '').length > 0",
            timeout=10_000)
        assert page.text_content("#status").strip() == "Учётная запись в архиве."
    finally:
        context.close()

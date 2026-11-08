"""Контракт оболочки: токен и слова входа — из `static/webapp-shell.js`.

Оба дефекта 31.10.2026 статический grep по `function api` не видел:

* `pages/files.py` читал `token` из старого ядра `admin.py`. Ядро разложили,
  имя исчезло, и кнопка «Скачать архивом» падала с ReferenceError **до** запроса;
* страницы звали `enter()` (boolean) и на отказе во входе печатали своё
  «Нет доступа», теряя причину, которую прислал сервер.

Тесты кликают по настоящей кнопке и читают настоящий текст статуса. Включаются
вместе с остальным набором:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations

import json

from tests.webapp.conftest import TELEGRAM_HASH, VIEWPORT


def test_архив_качается_после_клика(app_page):
    """Кнопка «Скачать архивом» шлёт запрос с Bearer оболочки и не падает."""
    page = app_page("/webapp/folder?id=7")
    page.wait_for_selector("#zip", state="visible", timeout=10_000)

    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    seen: dict[str, str] = {}

    def handle(route):
        seen["auth"] = route.request.headers.get("authorization", "")
        route.fulfill(status=200, content_type="application/zip", body=b"PK\x03\x04")

    page.route("**/webapp/folder/7/zip", handle)
    # Ждём сам запрос: старый код падал с ReferenceError до `fetch`, и запроса
    # не было вовсе — именно это и должен ловить тест.
    with page.expect_request("**/webapp/folder/7/zip", timeout=10_000):
        page.click("#zip")
    page.wait_for_function(
        "() => (document.getElementById('status').textContent || '')"
        ".indexOf('Архив скачивается') === 0",
        timeout=10_000)

    assert not errors, f"ошибки JS на кнопке архива: {errors}"
    assert seen.get("auth"), "запрос архива ушёл без Authorization"
    assert seen["auth"] == "Bearer " + page.evaluate("() => window.SVOI.token")


def test_отказ_архива_показывает_текст_сервера(app_page):
    """413 на архиве: человек видит `detail` конверта, а не «код 413» (FR-M6).

    tool-api объясняет потолок архива словами «Архив больше N МБ — скачивайте
    по файлам» (docs/architecture/85). Страница качает архив потоком сама, мимо
    `api()`, и раньше печатала только код — причину приходилось угадывать.
    """
    page = app_page("/webapp/folder?id=7")
    page.wait_for_selector("#zip", state="visible", timeout=10_000)

    detail = "Архив больше 100 МБ — скачивайте по файлам."
    page.route(
        "**/webapp/folder/7/zip",
        lambda route: route.fulfill(
            status=413, content_type="application/json",
            body=json.dumps({"code": "folder_zip_too_large", "detail": detail,
                             "title": "Слишком большой запрос"})))

    page.click("#zip")
    page.wait_for_function(
        "() => (document.getElementById('status').textContent || '')"
        ".indexOf('Архив больше') === 0",
        timeout=10_000)

    shown = page.text_content("#status").strip()
    assert shown == detail
    assert "код 413" not in shown


def test_не_json_отказ_архива_оставляет_код(app_page):
    """Если тело не разобрать (шлюз, прокси), честный запасной «код N» остаётся."""
    page = app_page("/webapp/folder?id=7")
    page.wait_for_selector("#zip", state="visible", timeout=10_000)

    page.route(
        "**/webapp/folder/7/zip",
        lambda route: route.fulfill(status=413, content_type="text/html",
                                    body="<h1>413</h1>"))

    page.click("#zip")
    page.wait_for_function(
        "() => (document.getElementById('status').textContent || '')"
        ".indexOf('Архив не отдался') === 0",
        timeout=10_000)

    assert "код 413" in page.text_content("#status")


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

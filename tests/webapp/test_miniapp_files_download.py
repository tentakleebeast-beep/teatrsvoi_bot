"""Скачивание одного файла из Mini App: кнопка, ссылка, отказы (Б2, К2–К6).

До карты Б2 единственным действием на экране папки был архив: он уводил страницу
на `blob:`-адрес и в WKWebView заменял документ пустым — на тёмной теме это
чёрный экран (дизайн 151 §2.3–2.4). Теперь «Скачать» в строке берёт
presigned-ссылку `GET /webapp/files/{id}/link` и открывает её браузером телефона
через `tg.openLink`; страница остаётся на месте.

Проверяется живьём — в настоящем HTML/CSS/JS проекта через стенд `tests/webapp/server.py`:

* кнопка есть в каждой строке папки и на экране файла, 44×44, со SVG;
* нажатие в строке не открывает файл и не меняет адрес, звук не играет;
* журнал `/stand/link-calls/{id}` растёт, `__opened` получает адрес из ответа;
* архива в Mini App нет ни на одной папке: `#zip` и текста «Скачать архивом» нет,
  `URL.createObjectURL` не вызывается;
* 404 говорит словами, 500 даёт баннер оболочки, а не пустой экран.

Обоснование: дизайн 151 §2–§6 (ветка `wt/b1-download`), критерии К2–К6, К8,
`docs/development/86-files-and-storage.md`, карта t_9c6ce885.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import json
import time

import pytest


def _tracks(page, count: int) -> None:
    """Ждёт строки папки: их число — доказательство, что экран загрузил файлы."""
    page.wait_for_function(
        "(n) => document.querySelectorAll('#tracks .ui-row').length === n",
        arg=count, timeout=10_000)


def _stand(page, path: str) -> dict:
    """Читает журнал стенда (`/stand/...`) — последствие действия, а не картинка."""
    base = page.url.split("/webapp/")[0]
    return page.request.get(f"{base}/stand/{path}").json()


def _wait_stand(page, path: str, ready, timeout: float = 5.0) -> dict:
    """Ждёт запись в журнале стенда: без неё экран мог перестроить только DOM."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _stand(page, path)
        if ready(data):
            return data
        page.wait_for_timeout(100)
    raise AssertionError(f"стенд не записал {path}: {_stand(page, path)}")


def _record_open(page) -> None:
    """Подменяет `openLink` до нажатия: адрес, который ушёл бы в браузер, — в `__opened`."""
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")


def _opened(page) -> str:
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    return page.evaluate("() => window.__opened")


def test_кнопка_в_строке_качает_файл_и_не_уводит_страницу(app_page) -> None:
    """К2–К4: нажатие в строке выдаёт ссылку и открывает её, адрес не меняется."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page, 3)
    _stand(page, "link-calls/31?reset=1")
    _record_open(page)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    # К3: кнопка в каждой строке папки.
    buttons = page.locator("#tracks .ui-row button[aria-label='Скачать']")
    assert buttons.count() == 3, "кнопка «Скачать» не в каждой строке"

    buttons.first.click()
    opened = _opened(page)
    # Имя в адресе закодировано процентами — сверяем путь файла и расширение.
    assert "/stand/download/31/" in opened and ".mp3" in opened, opened
    calls = _wait_stand(page, "link-calls/31", lambda data: data["count"] >= 1)
    assert calls["count"] == 1, calls

    assert page.url.split("?")[0].endswith("/webapp/folder"), page.url
    assert page.evaluate("() => document.body.textContent.length > 0"), "документ опустел"
    assert "Открываю в браузере" in page.inner_text("#status")
    assert not [url for url in requests if "/zip" in url], "экран снова запросил архив"
    assert not page.svoi_errors, page.svoi_errors


def test_кнопка_в_строке_не_открывает_файл(app_page) -> None:
    """К4: «Скачать» — отдельная цель, строка при этом не открывает файл и не играет."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page, 3)
    _record_open(page)

    page.locator("#tracks .ui-row").first.locator("button[aria-label='Скачать']").click()
    _opened(page)
    page.wait_for_timeout(300)

    assert "/webapp/file" not in page.url, "скачивание увело на экран файла"
    assert page.locator("#now").is_hidden(), "скачивание запустило воспроизведение"
    assert "Играет" not in page.inner_text("#status")
    assert not page.svoi_errors, page.svoi_errors


def test_на_экране_файла_есть_кнопка_скачать(app_page) -> None:
    """К5: на экране документа две кнопки; «Скачать» зовёт тот же маршрут."""
    page = app_page("/webapp/file?id=2")
    page.wait_for_selector(".ui-file__card", timeout=10_000)
    _stand(page, "link-calls/2?reset=1")
    _record_open(page)

    buttons = page.locator("#file-actions button")
    assert buttons.count() == 2, "на экране файла не две кнопки"
    assert buttons.all_inner_texts() == ["Открыть в браузере", "Скачать"]
    buttons.nth(1).click()

    opened = _opened(page)
    assert "2" in opened and ".pdf" in opened, opened
    calls = _wait_stand(page, "link-calls/2", lambda data: data["count"] >= 1)
    assert calls["count"] == 1, calls
    # Главной на экране остаётся просмотр (решение А3).
    primary = page.locator("#file-actions .ui-btn--primary")
    assert primary.inner_text() == "Открыть в браузере"
    assert not page.svoi_errors, page.svoi_errors


def test_архива_на_экране_папки_нет(app_page) -> None:
    """К8: ни кнопки архива, ни текста про него; `createObjectURL` не зовётся."""
    counts = {7: 3, 8: 2, 12: 1}
    for folder_id, count in counts.items():
        page = app_page(f"/webapp/folder?id={folder_id}")
        # Обёртка ставится до загрузки: считаем попытки создать blob-адрес.
        page.add_init_script(
            "window.__blobCalls = 0;"
            " (function () { var original = URL.createObjectURL;"
            " URL.createObjectURL = function () { window.__blobCalls += 1;"
            " return original.apply(this, arguments); }; })();")
        page.reload(wait_until="load")
        _tracks(page, count)

        assert page.locator("#zip").count() == 0, "кнопка архива снова на экране"
        body = page.inner_text("body")
        assert "Скачать архивом" not in body, body
        assert "Архив" not in body, body
        assert page.evaluate("() => window.__blobCalls") == 0, "экран создал blob-адрес"
        assert not page.svoi_errors, page.svoi_errors


def test_отказ_404_говорит_словами(app_page) -> None:
    """К10/§6: 404 на ссылку — текст человека, строки папки на месте."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page, 3)
    page.route(
        "**/webapp/files/31/link",
        lambda route: route.fulfill(
            status=404, content_type="application/json",
            body=json.dumps({"ok": False, "code": "file_not_found",
                             "detail": "Файл не найден."})))

    page.locator("#tracks .ui-row").first.locator("button[aria-label='Скачать']").click()
    page.wait_for_function(
        "() => document.querySelector('#status').textContent"
        ".indexOf('Файл не найден. Возможно, его удалили.') >= 0", timeout=10_000)
    assert page.locator("#tracks .ui-row").count() == 3, "отказ стёр папку"
    assert not page.svoi_errors, page.svoi_errors


def test_нет_связи_даёт_баннер_а_не_пустой_экран(app_page) -> None:
    """§6/V-047 а: 500 на ссылке — одно сообщение баннера оболочки, `#status` пуст."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page, 3)
    page.route(
        "**/webapp/files/31/link",
        lambda route: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"ok": False, "code": "storage_unavailable",
                             "detail": "Сервис файлов не ответил."})))

    page.locator("#tracks .ui-row").first.locator("button[aria-label='Скачать']").click()
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)
    assert page.locator("#net-error button").inner_text().strip() == "Повторить"
    assert page.inner_text("#status").strip() == "", page.inner_text("#status")
    assert page.evaluate("() => document.body.textContent.length > 0"), "документ опустел"
    assert page.locator("#tracks .ui-row").count() == 3
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("path", ["/webapp/folder?id=8", "/webapp/folder?id=12"])
def test_архив_не_предлагается_на_разных_папках(app_page, path: str) -> None:
    """К8: папка из двух документов и папка с одним большим файлом — без архива."""
    page = app_page(path)
    page.wait_for_function(
        "() => document.querySelectorAll('#tracks .ui-row').length > 0", timeout=10_000)
    assert page.locator("#zip").count() == 0
    assert "Скачать архивом" not in page.inner_text("body")
    assert not page.svoi_errors, page.svoi_errors

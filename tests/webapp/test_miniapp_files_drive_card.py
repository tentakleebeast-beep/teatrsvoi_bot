"""Карточка файла в Mini App: ссылка на Drive и описание (E4, V-123, V-124).

До E4 карточка знала только Garage: «Открыть в браузере» и «Скачать» рисовались для
любого файла, а «Скачать» у ссылки на Drive ответило бы 409. Здесь проверяется живьём —
в настоящем HTML/CSS/JS проекта через стенд `tests/webapp/server.py`:

* у ссылки на Drive одна кнопка — «Открыть в Drive», она уходит в `tg.openLink`;
* кнопки «Скачать» нет вовсе, а не «есть, но ругается»;
* пояснение «копии у студии нет» и путь в Drive на карточке;
* описание видно и правится владельцем через `/webapp/files/{id}/meta` (V-124);
* у чужой ссылки поля описания нет — чужой файл ответил бы 404.

Обоснование: V-123, V-124, docs/development/192 §6.4, §6.8, §8.4.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations


def _file_screen(page) -> None:
    page.wait_for_function(
        "() => document.querySelector('#file-name').textContent !== 'Файл'", timeout=10_000)


def _record_open(page) -> None:
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")


def _opened(page) -> str:
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    return page.evaluate("() => window.__opened")


def _stand(page, path: str) -> dict:
    base = page.url.split("/webapp/")[0]
    return page.request.get(f"{base}/stand/{path}").json()


def test_карточка_ссылки_открывает_drive_и_не_предлагает_скачать(app_page) -> None:
    """§8.4/V-123: одна кнопка «Открыть в Drive», без «Скачать», с пояснением и путём."""
    page = app_page("/webapp/file?id=11")
    _file_screen(page)
    _record_open(page)

    body = page.inner_text("body")
    assert "Это ссылка на Drive" in body, body
    assert "копии у студии нет" in body, body
    assert "Спектакли/Пурга" in body, body
    assert "Скачать" not in body, body
    assert "Открыть в браузере" not in body, body

    page.locator("#file-actions button", has_text="Открыть в Drive").click()
    opened = _opened(page)
    assert opened.startswith("https://drive.google.com/file/d/"), opened
    assert not page.svoi_errors, page.svoi_errors


def test_описание_видно_и_правится_владельцем(app_page) -> None:
    """V-124: поле «Что это» с текстом файла; сохранение идёт в `/meta`, статус — словами."""
    page = app_page("/webapp/file?id=11")
    _file_screen(page)
    assert page.locator("#file-edit").is_visible() is True
    assert page.eval_on_selector("#file-describe", "node => node.value") == \
        "Считает бухгалтер, правки до пятницы"

    page.fill("#file-describe", "Уточнил у бухгалтера")
    page.locator("#file-save").click()
    page.wait_for_function(
        "() => document.querySelector('#file-save-status').textContent.indexOf('сохранено') >= 0",
        timeout=10_000)
    calls = _stand(page, "meta-calls")
    assert calls["count"] == 1, calls
    assert calls["last"]["file_id"] == 11, calls
    assert calls["last"]["body"]["description"] == "Уточнил у бухгалтера", calls
    assert not page.svoi_errors, page.svoi_errors


def test_у_чужой_ссылки_поля_описания_нет(app_page) -> None:
    """V-124: чужой файл ответил бы 404 — кнопки, которая гарантированно ругается, нет."""
    page = app_page("/webapp/file?id=12")
    _file_screen(page)
    assert page.locator("#file-edit").is_hidden()

    assert page.locator("#file-actions button", has_text="Открыть в Drive").count() == 1
    assert "Скачать" not in page.inner_text("body")
    assert not page.svoi_errors, page.svoi_errors

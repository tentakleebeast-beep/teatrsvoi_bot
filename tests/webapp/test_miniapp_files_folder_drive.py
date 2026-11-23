"""Экран папки в Mini App: ссылка на Drive — ссылка, а не плеер (E4, §8.5).

До E4 строка папки была одной формы: любой файл получал кнопку «Скачать», а звук —
плеер. У ссылки на Drive байтов нет, и скачивание ответило бы 409. Здесь проверяется
живьём — в настоящем HTML/CSS/JS проекта через стенд `tests/webapp/server.py`:

* строка `drive` показывает «в Drive» и кнопку «Открыть», а не плеер и не длительность;
* нажатие уходит в `tg.openLink` с адресом Drive, страница остаётся на месте;
* строки `garage` не изменились: плеер и «Скачать» на месте;
* поиск внутри папки идёт с `folder=<полный путь>` — так отвечает «что в папке Пурга».

Обоснование: V-123, FR-K17, docs/development/192 §8.3, §8.5.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations


def _tracks(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('#tracks .ui-row').length === n",
        arg=count, timeout=10_000)


def _opened(page) -> str:
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    return page.evaluate("() => window.__opened")


def _stand(page, path: str) -> dict:
    base = page.url.split("/webapp/")[0]
    return page.request.get(f"{base}/stand/{path}").json()


def test_строка_drive_открывает_ссылку_а_не_играет(app_page) -> None:
    """§8.5: у строки ссылки «в Drive» и «Открыть», ни плеера, ни длительности."""
    page = app_page("/webapp/folder?id=10")
    _tracks(page, 4)
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")

    row = page.locator("#tracks .ui-row", has_text="Пурга — папка в Drive").first
    text = row.inner_text()
    assert "в Drive" in text, text
    assert "папка" in text, text
    assert row.locator("audio").count() == 0, "ссылка получила плеер"
    assert row.locator("#player").count() == 0, "ссылка получила плеер"

    before = page.url
    row.locator("button", has_text="Открыть").click()
    opened = _opened(page)
    assert opened.startswith("https://drive.google.com/drive/folders/"), opened
    assert page.url == before, "строка ушла с экрана вместо открытия ссылки"
    assert not page.svoi_errors, page.svoi_errors


def test_строки_garage_не_изменились(app_page) -> None:
    """§8.5: звук по-прежнему играет, у документа есть «Скачать»."""
    page = app_page("/webapp/folder?id=10")
    _tracks(page, 4)
    audio_row = page.locator("#tracks .ui-row", has_text="01 Увертюра.mp3").first
    assert audio_row.locator("button[aria-label='Скачать']").count() == 1
    assert page.locator("#play-all").is_visible() is True
    assert not page.svoi_errors, page.svoi_errors


def test_поиск_в_папке_идёт_с_путём_папки(app_page) -> None:
    """§8.3: запрос уходит с `folder=<путь>` — иначе «что в папке Пурга» не ответить."""
    page = app_page("/webapp/folder?id=10")
    _tracks(page, 4)
    _stand(page, "search-calls?reset=1")
    page.fill("#q", "Пурга")
    page.wait_for_function(
        "() => document.querySelector('#subtitle').textContent.indexOf('Найдено:') === 0",
        timeout=10_000)
    calls = _stand(page, "search-calls")
    assert calls["count"] >= 1, calls
    assert calls["last"]["folder"] == "Спектакль/Пурга", calls
    assert calls["last"]["q"] == "Пурга", calls
    assert not page.svoi_errors, page.svoi_errors

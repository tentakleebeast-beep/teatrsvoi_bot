"""Просмотр файлов в Mini App: картинка, звук, документ, отказы (А3, К1–К12).

До карты А3 файл в приложении открыть было нельзя: строка списка не имела
обработчика, а pdf в папке уходил в `<audio src>`. Здесь проверяется поведение
живьём — в настоящем HTML/CSS/JS проекта через стенд `tests/webapp/server.py`:

* строка «Файлов» открывает экран файла, «Удалить» и шеврон остаются отдельными целями;
* картинка укладывается в экран без прокрутки при имени в одну и в две строки;
* документ и текст показывают карточку и «Открыть в браузере» (свежая ссылка + openLink);
* строка папки ведёт в файл, который действительно открывается: картинка рисуется,
  документ показывает карточку (не «Файл не найден»);
* «Слушать всё» и автопереход идут только по звуку — pdf между треками очередь не ломает;
* стрелки порядка меняют строки местами и шлют `/order` с новым списком id;
* «Скачать архивом» отдаёт поток и пишет «Архив скачивается.», а отказ 413 — словами;
* истёкшая ссылка повторяется ровно один раз, затем понятная заглушка;
* 404/410/403 говорят словами, кнопок не предлагают;
* папка без звука не заводит ни «Слушать всё», ни плеера.

Обоснование: docs/development/150-miniapp-file-view.md §5–§7, критерии К1–К12,
`docs/development/86-files-and-storage.md` §3.5, карта t_a97e9a0c.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import time

import pytest


def _rows(page) -> None:
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-row').length > 0", timeout=10_000)


def _file_screen(page) -> None:
    page.wait_for_function(
        "() => document.querySelector('#file-name').textContent !== 'Файл'", timeout=10_000)


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


def _opened_file(page, file_id: int) -> None:
    """Проверяет, что по адресу файла открылся он сам, а не «Файл не найден»."""
    page.wait_for_url(f"**/webapp/file?id={file_id}", timeout=10_000)
    _file_screen(page)
    body = page.inner_text("body")
    assert "Файл не найден" not in body, body
    assert not page.svoi_errors, page.svoi_errors


def test_строка_списка_открывает_экран_файла(app_page) -> None:
    """К1: нажатие на имя файла уводит на `/webapp/file?id=N`."""
    page = app_page("/webapp/files")
    _rows(page)

    page.locator("#list .ui-row").nth(1).locator(".ui-row__main").click()
    page.wait_for_url("**/webapp/file?id=2", timeout=10_000)
    _file_screen(page)
    assert "документ" in page.inner_text("#scene"), page.inner_text("#scene")
    assert not page.svoi_errors, page.svoi_errors


def test_удаление_и_шеврон_не_открывают_файл(app_page) -> None:
    """К1, §5.2: три нажатия на одной строке — три разных исхода, не один."""
    page = app_page("/webapp/files")
    _rows(page)

    page.locator("#list .ui-row").nth(2).locator("button:has-text('Удалить')").click()
    page.wait_for_selector(".ui-sheet", timeout=5_000)
    assert page.url.split("#")[0].endswith("/webapp/files"), "«Удалить» увела на экран файла"
    page.locator(".ui-sheet button:has-text('Отмена')").click()

    page.locator("#list .ui-row").nth(2).locator(".ui-row__chevron").click()
    page.wait_for_url("**/webapp/folder?id=7", timeout=10_000)
    # Адрес папки — не доказательство: на новом экране должны быть её строки.
    _tracks(page, 3)
    assert "Спектакль/Музыка" in page.inner_text("#subtitle"), page.inner_text("#subtitle")
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("file_id", [3, 8], ids=["имя в одну строку", "имя в две строки"])
def test_картинка_без_прокрутки(app_page, file_id) -> None:
    """К8: высота документа равна окну, вбок не переполняет, картинка внутри сцены."""
    page = app_page(f"/webapp/file?id={file_id}")
    page.wait_for_function(
        "() => { var i = document.querySelector('#scene img');"
        " return i && i.complete && i.naturalWidth > 0; }", timeout=10_000)

    metrics = page.evaluate("""() => {
      const scene = document.querySelector('#scene').getBoundingClientRect();
      const img = document.querySelector('#scene img').getBoundingClientRect();
      return {height: document.documentElement.scrollHeight,
              width: document.documentElement.scrollWidth,
              innerHeight: window.innerHeight, innerWidth: window.innerWidth,
              scene: {top: scene.top, bottom: scene.bottom},
              img: {top: img.top, bottom: img.bottom, left: img.left, right: img.right}};
    }""")
    assert abs(metrics["height"] - metrics["innerHeight"]) <= 1, metrics
    assert metrics["width"] <= metrics["innerWidth"] + 1, metrics
    assert metrics["img"]["top"] >= metrics["scene"]["top"] - 1
    assert metrics["img"]["bottom"] <= metrics["scene"]["bottom"] + 1
    assert not page.svoi_errors, page.svoi_errors


def test_аудио_играет_и_не_зовёт_браузер(app_page) -> None:
    """§7.4: у звука единственное действие — плеер, «Открыть в браузере» нет.

    Плеер ищем по элементу и свойству `controls`, а не селектором `audio[controls]`
    и не по видимости: в Linux-WebKit `<audio>` без загруженного медиа имеет
    нулевую высоту, а от `about:blank` приходит `MEDIA_ERR_SRC_NOT_SUPPORTED` —
    экран делает свой единственный повтор и убирает сцену вместе с плеером.
    Поэтому стенд отдаёт настоящий WAV (`/stand/tone.wav`), а тест проверяет
    наличие элемента и включённые у него элементы управления.
    """
    page = app_page("/webapp/file?id=1")
    page.wait_for_selector("#scene audio", state="attached", timeout=10_000)
    player = page.evaluate(
        "() => { var a = document.querySelector('#scene audio');"
        " return a ? {count: document.querySelectorAll('#scene audio').length,"
        " controls: a.controls} : null; }")
    assert player == {"count": 1, "controls": True}, f"плеера с управлением нет: {player}"

    assert page.locator("#file-actions button").count() == 0
    assert "аудио" in page.inner_text("#file-meta")


def test_документ_открывается_в_браузере_свежей_ссылкой(app_page) -> None:
    """К10: карточка «документ» и открытие через `openLink` по новому `/view`."""
    page = app_page("/webapp/file?id=2")
    page.wait_for_selector(".ui-file__card", timeout=10_000)
    card = page.inner_text(".ui-file__card")
    assert "Гаммы для хора.pdf" in card and "документ" in card, card
    assert "Документ откроется в браузере телефона" in card, card

    before = page.request.get(f"{page.url.split('/webapp/file')[0]}/stand/view-calls/2").json()
    page.evaluate("() => { window.__opened = '';"
                  " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")
    page.locator("#file-actions button").click()
    page.wait_for_function("() => window.__opened && window.__opened.length > 0", timeout=5_000)
    assert "/stand/sample.pdf" in page.evaluate("() => window.__opened")
    after = page.request.get(f"{page.url.split('/webapp/file')[0]}/stand/view-calls/2").json()
    assert after["count"] == before["count"] + 1, "кнопка не взяла свежую ссылку"


def test_текст_и_неизвестный_вид_ведут_себя_как_документ(app_page) -> None:
    """К16, К11: `.txt` и неизвестный вид — та же карточка и та же кнопка."""
    text = app_page("/webapp/file?id=4")
    text.wait_for_selector(".ui-file__card", timeout=10_000)
    assert "Текст откроется в браузере телефона" in text.inner_text("#scene")

    other = app_page("/webapp/file?id=5")
    other.wait_for_selector(".ui-file__card", timeout=10_000)
    card = other.inner_text(".ui-file__card")
    assert "файл" in card and "Файл откроется в браузере телефона" in card, card
    assert other.locator("#scene audio").count() == 0
    assert other.locator("#scene img").count() == 0


def test_истёкшая_ссылка_повторяется_один_раз(app_page) -> None:
    """К9: один повтор `/view`, второй отказ — заглушка с «Открыть в браузере»."""
    page = app_page("/webapp/file?id=6")
    page.wait_for_function(
        "() => (document.querySelector('#scene').textContent || '')"
        ".indexOf('Не удалось показать картинку') >= 0", timeout=10_000)

    calls = page.request.get(f"{page.url.split('/webapp/file')[0]}/stand/view-calls/6").json()
    assert calls["count"] == 2, f"запросов к /view: {calls['count']} — повтор не один"
    assert page.locator("#file-actions button:has-text('Открыть в браузере')").count() == 1
    assert not page.svoi_errors, page.svoi_errors


def test_нет_файла_и_нет_архива_говорят_словами(app_page) -> None:
    """К12: 404 и 410 — слова человека и путь в «Файлы», без сцены и кнопок."""
    missing = app_page("/webapp/file?id=999")
    missing.wait_for_function(
        "() => document.body.innerText.indexOf('Файл не найден') >= 0", timeout=10_000)
    assert "Файл не найден. Возможно, его удалили." in missing.inner_text("body")
    assert missing.locator("#file-actions a:has-text('К файлам')").count() == 1
    assert missing.locator("#scene img").count() == 0

    cold = app_page("/webapp/file?id=7")
    cold.wait_for_function(
        "() => document.body.innerText.indexOf('ни в хранилище, ни в архиве') >= 0", timeout=10_000)
    assert "Файл не найден ни в хранилище, ни в архиве." in cold.inner_text("body")


def test_роль_без_права_читает_отказ(app_page) -> None:
    """§8.3: право отозвали — экран говорит словами, пустой сцены не остаётся."""
    page = app_page("/webapp/file?id=3", headers={"X-SVOI-Role": "troupe"})
    page.wait_for_function(
        "() => document.body.innerText.indexOf('Раздел доступен администратору') >= 0",
        timeout=10_000)
    assert "Раздел доступен администратору, организатору и мастеру." in page.inner_text("body")
    assert page.locator("#scene img").count() == 0
    assert page.locator("#file-actions button").count() == 0


def test_папка_из_документов_без_плеера(app_page) -> None:
    """К1 §6.1: pdf-папка не создаёт ни «Слушать всё», ни `<audio>`; архив есть."""
    page = app_page("/webapp/folder?id=8")
    _tracks(page, 2)

    assert page.locator("#tracks .ui-row").count() == 2
    assert page.locator("audio").count() == 0, "папка из pdf завела плеер"
    assert page.locator("#play-all").is_visible() is False
    assert page.locator("#zip").is_visible() is True
    assert "2 файла" in page.inner_text("#subtitle"), page.inner_text("#subtitle")
    assert "документ" in page.inner_text("#tracks")

    # Строка документа — не адрес, а открывшийся документ: карточка и кнопка.
    page.locator("#tracks .ui-row").first.click()
    _opened_file(page, 41)
    page.wait_for_selector(".ui-file__card", timeout=10_000)
    card = page.inner_text(".ui-file__card")
    assert "Гаммы для хора.pdf" in card and "документ" in card, card
    assert page.locator("#file-actions button:has-text('Открыть в браузере')").count() == 1


def test_папка_с_картинками_не_играет_а_открывает(app_page) -> None:
    """К2: клик по картинке в папке открывает экран файла, а не запускает плеер."""
    page = app_page("/webapp/folder?id=9")
    _tracks(page, 2)
    assert page.locator("#play-all").is_visible() is False
    assert page.locator("audio").count() == 0

    page.locator("#tracks .ui-row").first.click()
    _opened_file(page, 51)
    # На новом экране — сама картинка: `naturalWidth > 0` значит, что байты пришли.
    page.wait_for_function(
        "() => { var i = document.querySelector('#scene img');"
        " return i && i.complete && i.naturalWidth > 0; }", timeout=10_000)
    assert page.inner_text("#file-name") == "Афиша.png", page.inner_text("#file-name")


def test_смешанная_папка_играет_только_звук(app_page) -> None:
    """К3: «Слушать всё» есть, но документ в очередь воспроизведения не попадает."""
    page = app_page("/webapp/folder?id=10")
    _tracks(page, 3)
    assert page.locator("#play-all").is_visible() is True

    page.locator("#tracks .ui-row").nth(1).click()
    _opened_file(page, 41)
    page.wait_for_selector(".ui-file__card", timeout=10_000)
    assert "документ" in page.inner_text(".ui-file__card"), page.inner_text("#scene")


def test_автопереход_идёт_по_звуку_а_pdf_в_очередь_не_попадает(app_page) -> None:
    """К3, §6.5: между двумя audio лежит pdf — «ended» уводит на второй звук.

    Папка 11 (`audio 31, pdf 41, audio 32`): без пропуска не-звука очередь встала
    бы на pdf, и человек услышал бы тишину. Адрес у треков разный (`?track=N`),
    иначе `#player.src` не отличит первый звук от второго.
    """
    page = app_page("/webapp/folder?id=11")
    _tracks(page, 3)
    assert page.locator("#play-all").is_visible() is True

    page.locator("#play-all").click()
    page.wait_for_function(
        "() => { var n = document.querySelector('#now');"
        " return !n.hidden && n.textContent.indexOf('01 Увертюра.mp3') >= 0; }",
        timeout=10_000)
    first_src = page.evaluate("() => document.querySelector('#player').src")
    assert "track=1" in first_src, first_src
    assert "sample.pdf" not in first_src, first_src

    page.evaluate("() => document.querySelector('#player').dispatchEvent(new Event('ended'))")
    page.wait_for_function(
        "() => document.querySelector('#now').textContent.indexOf('02 Выход героя.mp3') >= 0",
        timeout=10_000)
    now = page.inner_text("#now")
    second_src = page.evaluate("() => document.querySelector('#player').src")
    assert "Играет: 3." in now, now
    assert "track=2" in second_src, second_src
    assert "pdf" not in now.lower() and "sample.pdf" not in second_src, (now, second_src)
    assert not page.svoi_errors, page.svoi_errors


def test_порядок_треков_меняет_строки_и_шлёт_order(app_page) -> None:
    """§6.6: «↑» меняет местами строки и отправляет новый список id, а не только DOM."""
    page = app_page("/webapp/folder?id=7")
    _tracks(page, 3)
    _stand(page, "folder-order/7?reset=1")

    titles = page.locator("#tracks .ui-row__title")
    assert titles.nth(0).inner_text() == "01 Увертюра.mp3", titles.all_inner_texts()

    page.locator("#tracks .ui-row").nth(1).locator("button[aria-label='Выше']").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#tracks .ui-row__title')[0].textContent"
        " === '02 Выход героя.mp3'", timeout=10_000)

    orders = _wait_stand(page, "folder-order/7", lambda data: data["count"] >= 1)
    assert orders["last"]["file_ids"] == [32, 31, 33], orders
    assert not page.svoi_errors, page.svoi_errors


def test_архив_скачивается_и_пишет_об_этом(app_page) -> None:
    """§6.6: «Скачать архивом» получает поток и начинает загрузку — это видно."""
    page = app_page("/webapp/folder?id=8")
    _tracks(page, 2)
    _stand(page, "zip-calls/8?reset=1")

    with page.expect_download(timeout=10_000) as caught:
        page.locator("#zip").click()
    download = caught.value
    assert download.suggested_filename.endswith(".zip"), download.suggested_filename

    page.wait_for_function(
        "() => document.querySelector('#status').textContent.indexOf('Архив скачивается.') >= 0",
        timeout=10_000)
    assert _wait_stand(page, "zip-calls/8", lambda data: data["count"] >= 1)["count"] >= 1
    assert not page.svoi_errors, page.svoi_errors


def test_архив_413_говорит_словами_а_не_пустым_экраном(app_page) -> None:
    """§6.6: потолок архива — отказ в `detail`, экран печатает его, а не пустое место."""
    page = app_page("/webapp/folder?id=12")
    _tracks(page, 1)
    _stand(page, "zip-calls/12?reset=1")

    page.locator("#zip").click()
    page.wait_for_function(
        "() => document.querySelector('#status').textContent"
        ".indexOf('Архив больше 100 МБ — скачивайте по файлам.') >= 0", timeout=10_000)
    status = page.inner_text("#status")
    assert "Архив больше 100 МБ" in status, status
    assert page.locator("#tracks .ui-row").count() == 1, "отказ архива стёр папку"
    assert _wait_stand(page, "zip-calls/12", lambda data: data["count"] >= 1)
    assert not page.svoi_errors, page.svoi_errors

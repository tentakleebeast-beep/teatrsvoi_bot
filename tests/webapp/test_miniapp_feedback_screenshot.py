"""Скриншот в обратной связи: блок вложения в форме и миниатюра в очереди (Э6).

Карта t_f00525ab, критерии К23–К25 и К28; дизайн `docs/development/153` §2.1, D5, D6.
Стенд поднимает настоящие HTML/CSS/JS проекта (`tests.webapp.server`), поэтому замеры
на экране — те же, что получит человек.

Что проверяется:

  * в форме есть поле файла и строка-кнопка со счётчиком, число берётся из `limits`
    ответа `/webapp/feedback/mine`, а не из разметки (К23);
  * выбор файла даёт миниатюру 56 px, «Убрать» её снимает, не-картинка отбивается
    словами, четвёртый файл не влезает (К23);
  * при трёх миниатюрах кнопка «Отправить» остаётся на первом экране 390×844 (К23);
  * отправка идёт `presign → PUT → POST` с `attachments`, при сбое скриншоты остаются
    и повторный запрос не тратит вторую подпись (К24);
  * в очереди разбора обращение со скриншотом — своя строка с миниатюрой справа,
    метка «со скриншотом», нажатие ведёт на экран файла; без скриншота разметка
    прежняя; 404 на файл не роняет очередь (К25);
  * на узком устройстве (Galaxy S9+, 320 px) блок не выдавливает «Отправить»
    за экран и миниатюры не наезжают друг на друга (К28).

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \\
        tests/webapp/test_miniapp_feedback_screenshot.py -q --browser chromium
"""

from __future__ import annotations

import time

import pytest

from tests.webapp import server

FEEDBACK = "/webapp/feedback"
QUEUE = "/webapp/admin/t/feedback"

HINT = "Скриншот видит только администратор."
TYPE_TEXT = ("К обращению можно приложить только изображение — png, jpeg, webp или gif.")
PNG = b"\x89PNG\r\n\x1a\n" + b"shot" * 64

TROUPE = {"X-SVOI-Role": "troupe"}


@pytest.fixture(autouse=True)
def _clean_stand(monkeypatch) -> None:
    """Журнал стенда и подменяемые коды ответов — на каждый тест свои."""
    server.FEEDBACK_CALLS.clear()
    server.FEEDBACK_POSTS.clear()
    server.FEEDBACK_UPLOADED.clear()
    monkeypatch.setattr(server, "FEEDBACK_SEND_STATUS", 200)
    monkeypatch.setattr(server, "FEEDBACK_PRESIGN_STATUS", 200)
    monkeypatch.setattr(server, "FEEDBACK_LIMITS", dict(server.FEEDBACK_LIMITS))


def _png(name: str = "shot.png") -> dict:
    return {"name": name, "mimeType": "image/png", "buffer": PNG}


def _settled(page) -> None:
    page.wait_for_load_state("networkidle")


def _pick(page, files: list[dict]) -> None:
    """Выбрать файлы так, как это делает человек: поле файла и событие change."""
    page.set_input_files("#shot-file", files)
    page.wait_for_timeout(150)


def _thumb_height(page) -> float:
    box = page.locator("#shot-list .shot__thumb").first.bounding_box()
    return round(box["height"], 1)


def test_форма_показывает_блок_вложения_и_подсказку(app_page) -> None:
    """К23: поле файла, слово «Приложить скриншот», счётчик и подсказка по умолчанию."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)

    field = page.locator('#feedback-form input[type="file"]')
    assert field.count() == 1, "в форме нет поля файла — приложить нечего"
    accept = field.get_attribute("accept") or ""
    for mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        assert mime in accept, f"accept без {mime}: {accept}"
    assert field.get_attribute("multiple") is not None

    assert "Приложить скриншот" in page.locator("#shot-pick").inner_text()
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 0 из 3"
    assert page.locator("#shot-status").inner_text().strip() == HINT
    assert page.locator("#shot-list").is_hidden()
    assert page.svoi_errors == [], page.svoi_errors


def test_счётчик_берёт_число_из_пределов_сервера(app_page, monkeypatch) -> None:
    """К23: `limits.files` приходит данными — страница число не хардкодит."""
    monkeypatch.setattr(server, "FEEDBACK_LIMITS",
                        {"files": 2, "bytes": 8 * 1024 * 1024,
                         "mime": ["image/png", "image/jpeg", "image/webp", "image/gif"]})
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)

    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 0 из 2"

    _pick(page, [_png("a.png"), _png("b.png")])
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 2 из 2"
    assert page.locator("#shot-pick").is_disabled(), "при пределе кнопка закрыта"


def test_выбор_миниатюра_и_убрать(app_page) -> None:
    """К23: один PNG — одна миниатюра 56 px и «1 из 3»; «Убрать» возвращает ноль."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)

    _pick(page, [_png("IMG_0421.png")])

    assert page.locator("#shot-list .shot__thumb").count() == 1
    assert _thumb_height(page) == 56, "миниатюра не 56 px"
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 1 из 3"

    remove = page.locator('#shot-list .shot__thumb button[aria-label*="Убрать"]')
    assert remove.count() == 1, "у миниатюры нет кнопки «Убрать»"
    remove.click()

    assert page.locator("#shot-list .shot__thumb").count() == 0
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 0 из 3"
    assert page.svoi_errors == [], page.svoi_errors


def test_не_картинка_отбивается_словами(app_page) -> None:
    """К23: `.pdf` не даёт миниатюры, в статусе — что можно приложить."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)

    _pick(page, [{"name": "smeta.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.7"}])

    assert page.locator("#shot-list .shot__thumb").count() == 0
    assert page.locator("#shot-status").inner_text().strip() == TYPE_TEXT


def test_четвёртый_файл_не_влезает(app_page, monkeypatch) -> None:
    """К23: три выбраны — «3 из 3», кнопка закрыта, четвёртый не добавляется."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)

    _pick(page, [_png(f"shot{i}.png") for i in range(4)])

    assert page.locator("#shot-list .shot__thumb").count() == 3
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 3 из 3"
    assert page.locator("#shot-pick").is_disabled()


def test_три_миниатюры_не_выдавливают_отправить(app_page) -> None:
    """К23: с тремя миниатюрами низ «Отправить» ≤ 844 px — действие на первом экране."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)
    _pick(page, [_png(f"shot{i}.png") for i in range(3)])

    box = page.locator("#send").bounding_box()
    bottom = box["y"] + box["height"]
    assert bottom <= 844, f"«Отправить» уехала за экран: низ {bottom:.0f} px (замер 823)"


def test_отправка_идёт_presign_put_post(app_page) -> None:
    """К24: порядок запросов и тело POST; при 200 экран очищается и благодарит."""
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)
    page.fill("#text", "Чёрный экран при открытии недели")
    _pick(page, [_png("IMG_0421.png")])

    # Медленный presign: пока он в полёте, кнопка отправки занята — так состояние
    # «Отправляю скриншот…» видно тесту, а не проскакивает за миллисекунду.
    page.route("**/webapp/feedback/presign",
               lambda route: (time.sleep(0.6), route.continue_()))
    page.click("#send")
    assert page.locator("#send").is_disabled(), "на время загрузки отправка закрыта"
    assert page.locator("#send").inner_text().strip() == "Отправляю скриншот…"

    page.wait_for_selector(".ui-toast", timeout=10_000)

    assert server.FEEDBACK_CALLS == ["presign", "PUT", "feedback"], server.FEEDBACK_CALLS
    sent = server.FEEDBACK_POSTS[-1]
    assert len(sent["attachments"]) == 1
    attachment = sent["attachments"][0]
    assert attachment["object_key"].startswith("p")
    assert attachment["name"] == "IMG_0421.png"
    assert attachment["mime"] == "image/png"
    assert len(attachment["sha256"]) == 64 and all(
        char in "0123456789abcdef" for char in attachment["sha256"])
    assert server.FEEDBACK_UPLOADED[attachment["object_key"]] == PNG

    assert "Спасибо, отправлено." in page.locator(".ui-toast").inner_text()
    assert page.locator("#text").input_value() == ""
    assert page.locator("#shot-list .shot__thumb").count() == 0
    assert page.locator("#shot-pick").inner_text().strip() == "Приложить скриншот · 0 из 3"
    assert page.svoi_errors == [], page.svoi_errors


def test_сбой_отправки_сохраняет_скриншот(app_page, monkeypatch) -> None:
    """К24: 500 — текст ошибки и миниатюры на месте; повтор не тратит вторую подпись."""
    monkeypatch.setattr(server, "FEEDBACK_SEND_STATUS", 500)
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)
    page.fill("#text", "Не уходит обращение")
    _pick(page, [_png("IMG_0421.png")])

    page.click("#send")
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)

    assert page.locator("#error").inner_text().strip() == \
        "Не отправилось. Проверьте сеть и повторите."
    assert page.locator("#shot-list .shot__thumb").count() == 1, "миниатюра пропала"
    assert server.FEEDBACK_CALLS == ["presign", "PUT", "feedback"], server.FEEDBACK_CALLS
    key = server.FEEDBACK_POSTS[-1]["attachments"][0]["object_key"]

    monkeypatch.setattr(server, "FEEDBACK_SEND_STATUS", 200)
    page.click("#send")
    page.wait_for_selector(".ui-toast", timeout=10_000)

    assert server.FEEDBACK_CALLS == ["presign", "PUT", "feedback", "feedback"], \
        "повторная отправка заново просит подпись"
    assert server.FEEDBACK_POSTS[-1]["attachments"][0]["object_key"] == key


def test_без_права_форма_скрывается(app_page, monkeypatch) -> None:
    """К24: 403 на presign — форма скрыта, экран говорит, кто принимает обращения."""
    monkeypatch.setattr(server, "FEEDBACK_PRESIGN_STATUS", 403)
    page = app_page(FEEDBACK, headers=TROUPE)
    _settled(page)
    page.fill("#text", "Право сняли на ходу")
    _pick(page, [_png("IMG_0421.png")])

    page.click("#send")
    page.wait_for_selector("#feedback-denied:not([hidden])", timeout=10_000)

    assert page.locator("#feedback-form").is_hidden()
    assert "Обращения принимает организатор" in page.locator("#feedback-denied").inner_text()


@pytest.fixture()
def queue(monkeypatch):
    """Очередь разбора на стенде: обращение со скриншотом и без."""
    monkeypatch.setattr(server, "FEEDBACK_QUEUE", list(server.FEEDBACK_QUEUE_SAMPLE))
    return server.FEEDBACK_QUEUE_SAMPLE


def test_очередь_миниатюра_справа_от_текста(app_page, queue) -> None:
    """К25: своя строка `ui-row feedback-row`, миниатюра справа, метка и адрес."""
    page = app_page(QUEUE)
    _settled(page)

    shot_row = page.locator(".feedback-row").first
    assert shot_row.count() == 1, "обращение со скриншотом потеряло свою строку"
    assert "admin-row--stack" not in (shot_row.get_attribute("class") or "")

    main = shot_row.locator(".ui-row__main").first.bounding_box()
    thumb = shot_row.locator(".feedback-shot").first.bounding_box()
    assert thumb["x"] > main["x"] + main["width"] - 1, "миниатюра не справа от текста"
    assert round(thumb["width"]) == 56 and round(thumb["height"]) == 56
    assert main["width"] >= 240, f"текстовая колонка сузилась до {main['width']:.0f} px"

    row = shot_row.bounding_box()
    assert row["height"] < 140, f"строка разъехалась: {row['height']:.0f} px (было 108)"

    assert "со скриншотом" in shot_row.inner_text()
    assert shot_row.locator('.feedback-shot[href="/webapp/file?id=91"]').count() == 1
    assert shot_row.locator(".feedback-shot img").count() == 1, "картинка не нарисовалась"
    assert page.svoi_errors == [], page.svoi_errors


def test_очередь_без_скриншота_разметка_прежняя(app_page, queue) -> None:
    """К25: у обращения без вложения — прежняя строка, без миниатюры и метки."""
    page = app_page(QUEUE)
    _settled(page)

    plain = page.locator(".ui-row.admin-row--stack").first
    assert plain.count() == 1, "разметка строки без скриншота изменилась"
    assert plain.locator(".feedback-shot").count() == 0
    assert "со скриншотом" not in plain.inner_text()
    height = plain.bounding_box()["height"]
    assert height <= 150, f"высота строки без скриншота уехала: {height:.0f} px"


def test_очередь_переживает_недоступный_файл(app_page, monkeypatch) -> None:
    """К25: 404 на `/webapp/files/{id}/view` — строка без миниатюры, метка остаётся."""
    broken = [
        {"id": 39, "kind": "bug", "text": "Пропавший скриншот", "status": "new",
         "resolution": None, "created_at": "2026-11-09T12:00:00", "resolved_at": None,
         "author": "Валерий",
         "attachments": [{"id": 92, "name": "lost.png", "mime": "image/png",
                          "size_bytes": 1000}]},
    ]
    monkeypatch.setattr(server, "FEEDBACK_QUEUE", broken)
    page = app_page(QUEUE)
    _settled(page)

    row = page.locator(".feedback-row").first
    assert "со скриншотом" in row.inner_text(), "метка пропала вместе с картинкой"
    assert page.locator(".feedback-shot").count() == 0, "миниатюра без картинки"
    assert "Пропавший скриншот" in row.inner_text(), "очередь не нарисовала обращение"
    assert page.svoi_errors == [], page.svoi_errors


def test_узкий_экран_не_ломает_блок(device_page) -> None:
    """К28: 320 px — «Отправить» не выдавливается за экран, миниатюры не наезжают."""
    page = device_page(FEEDBACK, device="Galaxy S9+")
    page.wait_for_load_state("networkidle")
    page.set_input_files("#shot-file", [_png(f"shot{i}.png") for i in range(3)])
    page.wait_for_timeout(150)

    thumbs = page.locator("#shot-list .shot__thumb")
    assert thumbs.count() == 3
    boxes = [thumbs.nth(index).bounding_box() for index in range(3)]
    for left, right in zip(boxes, boxes[1:], strict=False):
        assert left["x"] + left["width"] <= right["x"] + 0.5, "миниатюры наехали друг на друга"

    assert page.locator("#shot-list").evaluate(
        "node => node.scrollWidth <= node.clientWidth + 1"), "миниатюры шире экрана"
    assert page.locator("#shot-pick").bounding_box()["x"] + \
        page.locator("#shot-pick").bounding_box()["width"] <= 320 + 0.5
    assert page.svoi_errors == [], page.svoi_errors

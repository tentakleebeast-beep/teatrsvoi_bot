"""Экран «Типы и цвета»: форма по праву, диалог из трёх кнопок, текст перекраски.

Проверки держат AC-16…AC-21 документа `docs/development/190` §6 и решение V-087:
буква, цвет и порядок правятся на экране, сохранение всегда спрашивает человека
тремя равноправными кнопками, «Только новые» не пишет в Google, а «Перекрасить
все» отчитывается числом «Перекрашено N из M».

Про режим `target`: решение владельца V-120 б — перекраска работает и в боевом
календаре. Ключ записи `calendar.live` объявлен в реестре `google_targets` и действует
только в целевом режиме, поэтому в `target` «Перекрасить все» сохраняет настройку и
правит боевой календарь, а экран показывает «Сохранено. Перекрашено N из M», а не отказ.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import json

MASTER = {"X-SVOI-Role": "master"}
TARGET = {"X-Svoi-Google-Mode": "target"}

SAVE = "/webapp/events/types"
RECOLOR_ALL = "Перекрасить все"
ONLY_NEW = "Только новые"
CANCEL = "Отмена"


def _saves(page) -> list[dict]:
    """Тела POST на сохранение настройки: «Отмена» не должна дать ни одного."""
    calls: list[dict] = []
    page.on("request", lambda request: calls.append(json.loads(request.post_data or "{}"))
            if request.method == "POST" and request.url.endswith(SAVE) else None)
    return calls


def _open(page, headers=None):
    """Открывает настройку и ждёт, пока форма наполнится строками типов."""
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#types-form:not([hidden])", timeout=10_000)
    return page


def test_организатор_видит_форму_и_скрытые_типы_отдельно(app_page) -> None:
    """Активные типы — в списке, неактивные — блоком «Скрытые» (190 §6.2).

    `project`, `meeting` и `other` не предлагаются при создании события, но их
    название и цвет правятся: строка остаётся, место — другое.
    """
    page = _open(app_page("/webapp/admin/t/event-types"))

    assert page.locator("#types .ev-type-row").count() == 4, "не все активные типы на экране"
    assert page.locator("#hidden-block").is_visible(), "неактивные типы не отделены"
    assert "Скрытые" in page.locator("#hidden-block").inner_text()
    assert page.locator("#types-hidden .ev-type-row").count() == 3
    assert page.locator("#save").is_visible()
    assert page.svoi_errors == [], page.svoi_errors


def test_буква_существующего_типа_только_для_чтения(app_page) -> None:
    """Буква неизменяема (409 `letter_immutable`): поле не даёт стереть метку."""
    page = _open(app_page("/webapp/admin/t/event-types"))

    first = page.locator("#types .ev-type-row .ev-letter").first
    assert first.input_value() == "А"
    assert first.get_attribute("readonly") is not None, "букву существующего типа можно править"


def test_роль_без_права_видит_объяснение_а_не_форму(app_page) -> None:
    """`master` не имеет `calendar.write`: форма скрыта, сказано, у кого настройка."""
    page = app_page("/webapp/admin/t/event-types", headers=MASTER)
    page.wait_for_selector("#denied:not([hidden])", timeout=10_000)

    assert page.locator("#types-form").is_hidden(), "роли без права показали форму"
    text = page.locator(".ui-screen").inner_text()
    assert "Типы и цвета — у организатора" in text, text
    assert page.svoi_errors == [], page.svoi_errors


def test_диалог_из_трёх_кнопок_без_значения_по_умолчанию(app_page) -> None:
    """V-087: два равноправных варианта и «Отмена», ни один не выделен."""
    page = _open(app_page("/webapp/admin/t/event-types"))
    page.locator("#save").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)

    labels = page.locator(".ui-sheet__actions .ui-btn").all_inner_texts()
    assert labels == [RECOLOR_ALL, ONLY_NEW, CANCEL], labels
    assert page.locator(".ui-sheet__actions .ui-btn--primary").count() == 0, (
        "в диалоге есть кнопка по умолчанию")
    assert "Применить цвета к событиям" in page.locator(".ui-sheet").inner_text()


def test_отмена_ничего_не_сохраняет(app_page) -> None:
    """Закрытие диалога = «Отмена»: ни одного POST на сохранение (190 §6.4)."""
    page = _open(app_page("/webapp/admin/t/event-types"))
    calls = _saves(page)
    page.locator("#save").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)
    page.locator(".ui-sheet__actions .ui-btn", has_text=CANCEL).click()
    page.wait_for_selector(".ui-sheet", state="detached", timeout=10_000)
    page.wait_for_timeout(300)

    assert calls == [], f"«Отмена» отправила сохранение: {calls}"


def test_только_новые_отправляет_apply_new_и_говорит_о_будущих_событиях(app_page) -> None:
    """«Только новые»: `apply: "new"`, в Google не пишем, текст про новые события."""
    page = _open(app_page("/webapp/admin/t/event-types"))
    calls = _saves(page)
    page.locator("#save").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)
    page.locator(".ui-sheet__actions .ui-btn", has_text=ONLY_NEW).click()
    page.wait_for_selector("#done:not([hidden])", timeout=10_000)

    assert len(calls) == 1, calls
    assert calls[0]["apply"] == "new", calls[0]
    assert len(calls[0]["types"]) == 7, "уходит не весь список типов"
    text = page.locator("#done").inner_text()
    assert text == "Сохранено. Цвета применятся к новым событиям.", text


def test_перекрасить_все_отчитывается_числом_и_причиной(app_page) -> None:
    """«Перекрасить все»: `apply: "all"` и честное «Перекрашено N из M» (AC-20)."""
    page = _open(app_page("/webapp/admin/t/event-types"))
    calls = _saves(page)
    page.locator("#save").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)
    page.locator(".ui-sheet__actions .ui-btn", has_text=RECOLOR_ALL).click()
    page.wait_for_selector("#done:not([hidden])", timeout=10_000)

    assert calls[0]["apply"] == "all", calls[0]
    text = page.locator("#done").inner_text()
    assert "Перекрашено 3 из 4" in text, text
    assert "не удалось: 1" in text, text
    assert "not_found" in text, text


def test_в_целевом_режиме_перекраска_отчитывается_числом(app_page) -> None:
    """V-120 б: в `target` «Перекрасить все» снова работает и отчитывается числом.

    Раньше экран получал 409 `recolor_target_forbidden` и называл выход «Только новые».
    После ключа записи `calendar.live` отказ снят: кнопка на месте, сохранение проходит,
    текст — «Сохранено. Перекрашено N из M» с причиной частичного сбоя.
    """
    page = _open(app_page("/webapp/admin/t/event-types", headers=TARGET))
    calls = _saves(page)
    page.locator("#save").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)

    button = page.locator(".ui-sheet__actions .ui-btn", has_text=RECOLOR_ALL)
    assert button.is_visible(), "в целевом режиме пропала «Перекрасить все»"
    button.click()
    page.wait_for_selector("#done:not([hidden])", timeout=10_000)

    assert calls[0]["apply"] == "all", calls[0]
    text = page.locator("#done").inner_text()
    assert "Перекрашено 3 из 4" in text, text
    assert "не удалось: 1" in text, text
    assert page.locator("#error").is_hidden(), "ошибка при успешной перекраске"

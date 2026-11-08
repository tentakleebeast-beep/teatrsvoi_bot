"""Поведение JS в браузере: экран делает то, что обещает, а не только «парсится».

`tests/unit/test_webapp_pages_js.py` проверяет синтаксис (`node --check`), и этого мало:
дефекты «форма пишет в несуществующую функцию» (`ef9e5cf`) и «хаб падает с KeyError: 0»
(`12e4517`) проходили с целым синтаксисом. Здесь три рискованных экрана проверяются
нажатиями: форма операции (`/webapp/tx`), панель «Люди» (`/webapp/admin/t/people`) и
карточка участника (`/webapp/person`).
Проверяем видимый эффект — вариант категорий в select, текст ошибки в поле, тост о
записи, число строк в списке, — а не наличие функции в исходнике.

`node --check` остаётся рядом: синтаксис и поведение — разные оси.

Обоснование: docs/reports/104-test-audit.md §5.2 (P1 «поведенческие проверки JS вместо
`node --check`»), docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations

import json

import pytest


def _options(page, selector: str) -> list[str]:
    return page.eval_on_selector_all(
        selector, "nodes => nodes.map(node => node.value).filter(Boolean)")


@pytest.fixture()
def tx_page(app_page):
    """Форма операции с загруженными категориями: ждём ответ /webapp/categories."""
    page = app_page("/webapp/tx")
    page.wait_for_function(
        "() => document.querySelectorAll('#tx-category option').length > 1", timeout=10_000)
    return page


def test_форма_операции_переключает_категории_по_типу(tx_page):
    """Доход нельзя записать в расходную статью: список пересобирается при смене типа."""
    expense = _options(tx_page, "#tx-category option")
    assert "Аренда помещения" in expense, expense

    tx_page.click('#tx-type [data-type="income"]')
    tx_page.wait_for_function(
        "() => Array.from(document.querySelectorAll('#tx-category option'))"
        ".some(option => option.value === 'Взносы студийцев')", timeout=5_000)

    income = _options(tx_page, "#tx-category option")
    assert "Взносы студийцев" in income, income
    assert "Аренда помещения" not in income, income
    assert tx_page.get_attribute('#tx-type [data-type="income"]', "aria-pressed") == "true"


def test_форма_операции_пишет_отказ_в_элемент(tx_page):
    """Пустая сумма — отказ рядом с полем, а не пустая строка и не «undefined»."""
    tx_page.click("#tx-save")
    tx_page.wait_for_selector("#err-amount:not([hidden])", timeout=5_000)

    text = tx_page.inner_text("#err-amount").strip()
    assert text, "поле ошибки пустое: человек не видит, почему запись не ушла"
    assert "undefined" not in text, text
    assert "сумм" in text.lower(), text


def test_форма_операции_показывает_итог_записи(tx_page):
    """Успешная запись видна словами: тост «Записано: …», поля очищены."""
    tx_page.fill("#tx-amount", "3 000")
    tx_page.select_option("#tx-category", "Аренда помещения")
    tx_page.click("#tx-save")

    toast = tx_page.wait_for_selector(".ui-toast__text", timeout=10_000)
    text = toast.inner_text().strip()
    assert text.startswith("Записано:"), text
    # в тосте сумма набрана неразрывными пробелами — сравниваем с обычными
    assert "3 000" in text.replace("\xa0", " "), text
    assert "Аренда помещения" in text, text
    assert tx_page.input_value("#tx-amount") == ""


def test_без_права_на_запись_формы_нет(app_page):
    """У роли без `budget.write` экран не рисует форму: отказ приходит до заполнения.

    Обоснование: docs/92 §2 (экран «Деньги: операции» — по `budget.write`),
    docs/91 принципы 8 и 9.
    """
    page = app_page("/webapp/tx")
    page.route("**/webapp/home/data", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"ok": True, "name": "Студиец", "role": "troupe",
                         "role_label": "труппа", "can_record": False})))
    page.reload(wait_until="load")
    page.wait_for_selector("#tx-denied:not([hidden])", timeout=10_000)

    assert not page.is_visible("#tx-form"), "форма видна роли без права на запись"
    assert not page.is_visible("#tx-save"), "кнопка «Записать» видна без права"
    assert not page.is_visible("#tx-amount"), "поле суммы видно без права"
    # Отказ словами и без «Записываю…» в тексте экрана: заполнять нечего.
    body = page.inner_text("body")
    assert "Операции записывает организатор" in body, body
    assert "Записать" not in body, body


def test_без_права_обратной_связи_формы_нет(app_page):
    """У мастера нет `feedback.send`: формы, поля комментария и «Отправить» нет.

    Стенд отдаёт `can_send: false` в данных (`X-SVOI-Role: master`), как прод
    после карты t_4f0c64d6. Обоснование: db/init/031 и docs/development/141 §3
    (`feedback.send` только у admin/org/troupe), docs/development/91 принципы 9 и Б3.
    """
    page = app_page("/webapp/feedback", headers={"X-SVOI-Role": "master"})
    page.wait_for_selector("#feedback-denied:not([hidden])", timeout=10_000)

    assert not page.is_visible("#feedback-form"), "форма видна роли без права"
    assert not page.is_visible("#text"), "поле комментария видно без права"
    assert not page.is_visible("#send"), "кнопка «Отправить» видна без права"
    body = page.inner_text("body")
    assert "Обращения принимает организатор" in body, body
    assert "Отправить" not in body, body
    assert not page.svoi_errors, page.svoi_errors


def test_с_правом_обратной_связи_форма_на_месте(app_page):
    """У труппы `feedback.send` есть: форма и поле комментария показываются.

    Обоснование: db/init/031 (право у admin/org/troupe), карта t_4f0c64d6.
    """
    page = app_page("/webapp/feedback")
    page.wait_for_selector("#feedback-form:not([hidden])", timeout=10_000)

    assert page.is_visible("#text"), "поле комментария скрыто у роли с правом"
    assert page.is_visible("#send"), "кнопка «Отправить» скрыта у роли с правом"
    assert not page.is_visible("#feedback-denied"), "форма и «нет права» одновременно"
    assert not page.svoi_errors, page.svoi_errors


def test_панель_люди_рисует_строки_и_ищет(app_page):
    """Панель не падает молча: строки из overview рисуются, поиск сужает список."""
    page = app_page("/webapp/admin/t/people")
    page.wait_for_function(
        "() => document.querySelectorAll('#people-list .ui-row').length === 2", timeout=10_000)
    assert "Иванов Виктор" in page.inner_text("#people-list")

    page.fill("#people-search", "Валерий")
    page.wait_for_function(
        "() => document.querySelectorAll('#people-list .ui-row').length === 1", timeout=5_000)

    assert "найдено 1" in page.inner_text("#people-count")
    assert "Валерий" in page.inner_text("#people-list")
    assert page.inner_text("#status").strip() == "", "на успешной загрузке статус пуст"


@pytest.fixture()
def person_page(app_page):
    """Карточка участника с загруженными данными: ждём имя и заполненный тариф."""
    page = app_page("/webapp/person?id=30")
    page.wait_for_function(
        "() => document.querySelectorAll('#facts .ui-dl__row').length >= 5", timeout=10_000)
    return page


def test_карточка_участника_показывает_данные(person_page):
    """Карточка на общей оболочке: шапка с «‹», факты человека, тариф из API."""
    assert person_page.inner_text(".ui-appbar__title").strip() == "Карточка участника"
    assert person_page.get_attribute(".ui-appbar__back", "href") == "/webapp/home"

    body = person_page.inner_text("body")
    assert "Тестовый Участник" in body, body
    assert "тестов" in body, "нет ключа человека"
    assert person_page.input_value("#tariff") == "5000", "тариф не подставлен из API"
    assert "Текущий месяц" in person_page.inner_text("#month")


def test_карточка_участника_сохраняет_тариф(person_page):
    """Сохранение видно словами: статус «Тариф сохранён…», а не пустая строка."""
    person_page.fill("#tariff", "7000")
    person_page.click("#send")

    # Сначала статус говорит «Сохраняю…», поэтому ждём именно итоговый текст.
    person_page.wait_for_function(
        "() => (document.getElementById('status').textContent || '')"
        ".indexOf('Тариф сохранён') === 0", timeout=10_000)
    text = person_page.inner_text("#status").strip()
    assert "Тариф сохранён" in text, text
    assert "undefined" not in text, text
    assert person_page.is_visible("#status"), "статус не виден: человек не узнает о записи"


def test_карточка_участника_пишет_отказ_словами(person_page):
    """Отказ сервера — русским текстом рядом с полем, без кода и «undefined»."""
    person_page.fill("#tariff", "1000000")      # стенд отвечает 422 разбором поля
    person_page.click("#send")

    person_page.wait_for_function(
        "() => (document.getElementById('status').textContent || '').indexOf('Не удалось') === 0",
        timeout=10_000)
    text = person_page.inner_text("#status").strip()
    assert "undefined" not in text, text
    assert "[object Object]" not in text, text
    assert "no_webapp_token" not in text, text
    assert "Тариф" in text, text
    assert person_page.is_visible("#status"), "отказ не виден: человек не поймёт, почему не сохранилось"

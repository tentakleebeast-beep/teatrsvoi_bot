"""Экран «Приглашения»: выбор человека, «Новый участник», персональный код (бота-прогон).

Спека — docs/development/182 §4.3.2 (V-116 б, V-117 а, V-108, V-106 б, V-105 а,
V-107 а). Формы «роль → код» больше нет: сначала человек (поиск по людям без
Telegram), потом код на него. Нового человека заводят шторкой «Новый участник»
с полями «Фамилия Имя» и ролью «Актёр · Организатор · Педагог».

Проверяется то, что видит администратор и что уходит на сервер: тело
`POST /webapp/admin/invite` несёт `participant_id`, в листе кода — имя человека,
а не роль; сегменты роли живут только в шторке, подсказка о дисциплине — только
у педагога.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \\
        tests/webapp/test_miniapp_invites_picker.py -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp import server as stand

INVITES = "/webapp/admin/t/invites"
NEW_PERSON = "Новый участник"
EMPTY = "Людей без Telegram нет — заведите кнопкой «Новый участник»."
HINT = "Дисциплину педагог выберет при регистрации."
SEGMENTS = "#new-person-role .ui-segmented__item"


@pytest.fixture(autouse=True)
def _reset_stand(webapp_server):
    stand.reset_invites()
    yield
    stand.reset_invites()


def _settled(page) -> None:
    page.wait_for_load_state("networkidle")


def _open_new_person(page) -> None:
    page.locator("#invite-new-person").click()
    page.wait_for_selector("#new-person-role", timeout=10_000)


def test_поиск_список_и_выпуск_без_выбора_заблокирован(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)

    assert page.locator("#invite-search").get_attribute("placeholder") == "Имя или фамилия"
    names = page.locator("#invite-people .ui-row__title").all_inner_texts()
    assert names, "список людей без Telegram пуст"
    assert "Кузнецова Мария" in names
    assert page.locator("#invite-new-person").inner_text().strip() == NEW_PERSON
    assert page.locator("#invite-issue").is_disabled(), "выпуск без выбора человека доступен"
    assert page.locator("#invite-new .ui-segmented__item").count() == 0, \
        "в форме выпуска снова выбирают роль"
    assert page.svoi_errors == [], page.svoi_errors


def test_поиск_сужает_список(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)

    page.fill("#invite-search", "сидор")
    page.wait_for_function(
        "() => document.querySelectorAll('#invite-people .ui-row').length === 1",
        timeout=10_000)
    names = page.locator("#invite-people .ui-row__title").all_inner_texts()
    assert names == ["Сидоров Пётр"], names


def test_выбор_человека_уходит_в_запрос_и_лист_печатает_имя(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)

    row = page.locator('#invite-people .ui-row[data-person="6"]')
    row.click()
    assert row.get_attribute("aria-pressed") == "true"
    assert page.locator("#invite-issue").is_enabled(), "выбор человека не включил выпуск"

    with page.expect_response(lambda r: r.url.endswith("/webapp/admin/invite")) as info:
        page.locator("#invite-issue").click()
    request = info.value.request
    assert request.post_data_json["participant_id"] == 6
    assert "role" not in request.post_data_json, "в теле снова роль вместо человека"

    page.wait_for_selector(".ui-sheet", timeout=10_000)
    sheet = page.locator(".ui-sheet").inner_text()
    assert stand.INVITE_CODE in sheet
    assert "Сидоров Пётр" in sheet, f"в листе нет имени человека: {sheet}"
    assert "труппа" in sheet
    assert "Роль:" not in sheet, f"лист снова печатает роль вместо имени: {sheet}"


def test_пустой_список_зовёт_к_новому_участнику(app_page) -> None:
    stand.reset_invites()
    stand.INVITE_CANDIDATES.clear()
    page = app_page(INVITES)
    _settled(page)

    assert EMPTY in page.locator("#invite-people").inner_text()


def test_шторка_нового_участника_роли_и_подсказка(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)
    _open_new_person(page)

    labels = [node.inner_text().strip() for node in page.locator(SEGMENTS).all()]
    assert labels == ["Актёр", "Организатор", "Педагог"], labels
    assert page.locator(SEGMENTS).first.get_attribute("aria-pressed") == "true", \
        "стартовая роль — не «Актёр»"
    hint = page.locator("#new-person-hint")
    assert not hint.is_visible(), "подсказка о дисциплине видна у актёра"

    page.locator(f'{SEGMENTS}[data-role="master"]').click()
    assert hint.is_visible() and hint.inner_text().strip() == HINT
    page.locator(f'{SEGMENTS}[data-role="org"]').click()
    assert not hint.is_visible(), "подсказка осталась у организатора"


def test_создание_человека_и_выпуск_кода_двумя_запросами(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)
    _open_new_person(page)

    page.fill("#new-person-name", "Новиков Артём")
    page.locator(f'{SEGMENTS}[data-role="master"]').click()

    with page.expect_response(lambda r: r.url.endswith("/webapp/admin/person-new")) as info:
        with page.expect_response(lambda r: r.url.endswith("/webapp/admin/invite")) as issued:
            page.locator(".ui-sheet .ui-btn--primary", has_text="Создать и выпустить код").click()

    assert info.value.request.post_data_json["display_name"] == "Новиков Артём"
    assert info.value.request.post_data_json["role"] == "master"
    assert issued.value.request.post_data_json["participant_id"]
    page.wait_for_selector(".ui-sheet", timeout=10_000)
    sheet = page.locator(".ui-sheet").inner_text()
    assert stand.INVITE_CODE in sheet and "Новиков Артём" in sheet
    assert "педагог" in sheet


def test_короткое_имя_оставляет_шторку_открытой_и_печатает_слова_сервера(app_page) -> None:
    page = app_page(INVITES)
    _settled(page)
    _open_new_person(page)

    page.fill("#new-person-name", "Артём")
    with page.expect_response(lambda r: r.url.endswith("/webapp/admin/person-new")) as info:
        page.locator(".ui-sheet .ui-btn--primary", has_text="Создать и выпустить код").click()
    assert info.value.status == 400
    page.wait_for_selector("#new-person-name", timeout=10_000)
    assert page.locator("#new-person-name").input_value() == "Артём"


def test_три_сегмента_на_320_без_обрезки(app_page) -> None:
    """V-106 б: кегль 14 px и поля 6 px — «Педагог» не подрезан на узком экране."""
    page = app_page(INVITES)
    page.set_viewport_size({"width": 320, "height": 658})
    _settled(page)
    _open_new_person(page)

    measure = page.evaluate("""() => {
      const box = document.getElementById('new-person-role');
      const inner = box.getBoundingClientRect();
      return {
        clipped: box.scrollWidth - box.clientWidth,
        items: Array.from(box.querySelectorAll('.ui-segmented__item')).map((node) => {
          const cs = getComputedStyle(node);
          const range = document.createRange();
          range.selectNodeContents(node);
          const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0);
          return {width: node.getBoundingClientRect().width,
                  needs: Math.max(...rects.map((r) => r.right)) -
                         Math.min(...rects.map((r) => r.left)) +
                         parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight),
                  lines: rects.length,
                  font: parseFloat(cs.fontSize)};
        }),
        inner: inner.width};
    }""")
    assert measure["clipped"] <= 0, f"ряд сегментов подрезан: {measure}"
    assert all(item["font"] == 14 for item in measure["items"]), measure
    assert all(item["lines"] == 1 for item in measure["items"]), measure
    assert all(item["needs"] <= item["width"] + 0.5 for item in measure["items"]), measure
    assert sum(item["width"] for item in measure["items"]) <= measure["inner"] + 1, measure

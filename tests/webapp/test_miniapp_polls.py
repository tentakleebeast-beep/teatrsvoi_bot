"""Экраны опросов в браузере: компактные строки, свёрнутый список, сводка, экспорт (E8).

Проверяется замером, а не на глаз (docs/development/162 §6 и §14.5):

* конструктор — строки 56 px, тип значением справа, в первом кадре 390×844 видно
  не меньше 8 вопросов из 11 (мокап `01b`, 1 255 px против 1 446);
* адресаты — люди свёрнуты до «в списке 8, показать всех», экран не растёт линейно
  по труппе;
* сводка на 11 вопросов — блок свёрнут до строки итога, без разворотов ≤ 1 000 px;
* целей нажатия меньше 44 px — 0, переполнения вбок — 0.

Обоснование: FR-G18, AC-15, `docs/development/162-bot-polls.md` §6, §14.5.
Движки: WebKit (iPhone) и Chromium (Pixel) — прогон дважды, как у остальных экранов.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_polls.py -q
"""

from __future__ import annotations

import pytest

TAP_MIN = 44

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")

VISIBLE_QUESTIONS = """
() => {
  const frame = window.innerHeight;
  return Array.from(document.querySelectorAll('#questions .ui-row'))
    .filter((row) => row.getBoundingClientRect().bottom <= frame + 1).length;
}
"""

SMALL_TARGETS = """
(min) => {
  const found = [];
  document.querySelectorAll('a, button, input, select, textarea').forEach((node) => {
    if (node.closest('[hidden]')) { return; }
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) { return; }
    if (rect.height < min - 0.5) {
      found.push((node.textContent || node.id || node.tagName).trim().slice(0, 40) +
                 ': ' + Math.round(rect.height) + 'px');
    }
  });
  return found;
}
"""


def _no_overflow(page) -> None:
    assert page.evaluate(OVERFLOW) <= 0, "экран переполнен по горизонтали"


def _tap_targets(page) -> None:
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "целей нажатия меньше 44 px: " + "; ".join(small)


def _height(page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollHeight"))


def test_список_опросов_и_отказ_по_праву(app_page) -> None:
    """Админ и организатор видят список; роль без `poll.manage` — слова отказа, а не сбой."""
    page = app_page("/webapp/polls")
    page.wait_for_selector("#polls .ui-row", timeout=10_000)
    assert page.locator("#polls .ui-row").count() == 2
    text = page.inner_text("#polls")
    assert "Анкета организаторам" in text and "идёт" in text
    assert page.locator("#denied").is_hidden()
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors

    # V-088 (б): организатор ведёт опросы сам — отказа на его роли быть не должно.
    organizer = app_page("/webapp/polls", headers={"X-SVOI-Role": "org"})
    organizer.wait_for_selector("#polls .ui-row", timeout=10_000)
    assert organizer.locator("#denied").is_hidden()
    assert not organizer.svoi_errors, organizer.svoi_errors

    denied = app_page("/webapp/polls", headers={"X-SVOI-Role": "master"})
    denied.wait_for_selector("#denied:not([hidden])", timeout=10_000)
    body = denied.inner_text("body")
    assert "Опросы ведут администратор и организатор" in body, body
    assert "Не удалось загрузить опросы" not in body, "403 подан как сбой сети"
    assert denied.locator("#form").is_hidden()


def test_конструктор_компактные_строки(app_page) -> None:
    """56 px, тип справа, 8 вопросов в первом кадре — как на мокапе `01b`."""
    page = app_page("/webapp/polls/edit?poll_id=7")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    rows = page.locator("#questions .ui-row")
    assert rows.count() == 11

    heights = page.evaluate(
        "() => Array.from(document.querySelectorAll('#questions .ui-row'))"
        ".map((row) => Math.round(row.getBoundingClientRect().height))")
    assert max(heights) <= 60, f"строка вопроса выше 56 px: {heights}"
    assert page.evaluate(VISIBLE_QUESTIONS) >= 8, "в первом кадре меньше 8 вопросов"
    assert _height(page) < 1_400, f"конструктор выше мокапа: {_height(page)} px"
    assert "один из списка" in page.inner_text("#questions")
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


def test_адресаты_свёрнуты_и_экран_не_растёт(app_page) -> None:
    """20 человек — в списке 8 и «показать всех»; высота не линейна по труппе."""
    page = app_page("/webapp/polls/edit?poll_id=7")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    page.click("#to-audience")
    page.wait_for_selector("#step-audience:not([hidden])", timeout=10_000)
    page.wait_for_function(
        "() => document.getElementById('audience-count').textContent.indexOf('Уйдёт') !== -1",
        timeout=10_000)

    assert page.locator("#people .ui-row").count() == 8
    assert "в списке 8 из 20" in page.inner_text("#people-all")
    height = _height(page)
    assert height <= 1_000, f"экран адресатов вырос: {height} px при 20 людях"
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors

    page.click("#people-all")
    assert page.locator("#people .ui-row").count() == 20


def test_сводка_свёрнута_и_экспорт(app_page) -> None:
    """11 вопросов — 11 свёрнутых блоков, документ без разворотов ≤ 1 000 px."""
    page = app_page("/webapp/polls/results?poll_id=7")
    page.wait_for_selector("#questions details", timeout=10_000)
    blocks = page.locator("#questions details")
    assert blocks.count() == 11
    opened = page.evaluate(
        "() => Array.from(document.querySelectorAll('#questions details'))"
        ".filter((node) => node.open).length")
    assert opened == 0, "блок вопроса раскрыт по умолчанию"

    height = _height(page)
    assert height <= 1_000, f"сводка на 11 вопросов выше 1 000 px: {height}"
    text = page.inner_text("#questions")
    assert "ответили 6" in text
    assert "выборов больше ответивших" in text

    csv = page.get_attribute("#csv", "href")
    md = page.get_attribute("#md", "href")
    assert "/webapp/polls/export" in csv and "format=csv" in csv
    assert "format=md" in md
    assert "6 из 14" in page.inner_text("#head")
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("device", ["iPhone 14", "Pixel 7"])
def test_экраны_опроса_на_двух_движках(device_page, device) -> None:
    """Один и тот же экран на iPhone (WebKit) и Pixel (Chromium): нет переполнения.

    `device_page` пропускает профиль чужого движка, поэтому полный прогон гоняет
    тест дважды — по разу на каждом (`--browser webkit` и `--browser chromium`).
    """
    for path in ("/webapp/polls", "/webapp/polls/edit?poll_id=7",
                 "/webapp/polls/results?poll_id=7"):
        page = device_page(path, device)
        page.wait_for_timeout(600)
        assert page.evaluate(OVERFLOW) <= 0, f"{device}: переполнение на {path}"
        assert not page.svoi_errors, (device, path, page.svoi_errors)

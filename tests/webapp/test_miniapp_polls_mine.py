"""«Мои опросы» в браузере: строка на Главной, список, экран вопроса (E8-D4).

Что проверяется замером, а не на глаз (`docs/development/164` §12.4–§12.6, §12.9):

* AC-1/AC-2 — строка «Мои опросы» с бейджем и подписью, переход в список;
* AC-4 — всё пройдено: бейджа нет, подпись «1 пройдено», строка осталась;
* AC-12 — прохождений нет: узла `#polls-entry` нет, список говорит словами;
* AC-13 — 390 и 320 px: один `ui-row`, рост страницы ≤ 105 px, переполнения вбок
  нет, JS-ошибок нет;
* AC-16 — «Пропустить» только у необязательного вопроса;
* AC-14/AC-15 — `multi` переключает варианты, неверное число не меняет вопрос.

Обоснование: FR-G18, AC-1, AC-2, AC-4, AC-12, AC-13, AC-14, AC-15, AC-16,
`docs/development/164-miniapp-home-polls.md` §12.4–§12.6, §12.9.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_polls_mine.py -q
"""

from __future__ import annotations

import urllib.request

import pytest

TAP_MIN = 44
HOME_GROWTH_MAX = 105

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")

SMALL_TARGETS = """
(min) => {
  const found = [];
  document.querySelectorAll('a, button, input, select, textarea').forEach((node) => {
    if (node.closest('[hidden]')) { return; }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') { return; }
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

READY = ("() => { const node = document.getElementById('name');"
         " return !!node && node.textContent.trim().length > 0; }")


def _reset(base: str) -> None:
    """Прохождение стенда — одно на сессию: перед экраном вопроса начинаем сначала."""
    request = urllib.request.Request(f"{base}/__polls/reset", data=b"", method="POST")
    with urllib.request.urlopen(request) as response:
        assert response.status == 200


def _wait_home(page) -> None:
    page.wait_for_function(READY, timeout=10_000)


def _height(page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollHeight"))


def _no_overflow(page) -> None:
    assert page.evaluate(OVERFLOW) <= 0, "экран переполнен по горизонтали"


def _tap_targets(page) -> None:
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "целей нажатия меньше 44 px: " + "; ".join(small)


def test_строка_ведёт_в_список_своих_опросов(app_page) -> None:
    """AC-1, AC-2: бейдж «1», подпись «1 ждёт ответа», переход в «Ждут ответа»."""
    page = app_page("/webapp/home", headers={"X-SVOI-Polls": "one"})
    page.wait_for_selector("#polls-entry .ui-row", timeout=10_000)
    entry = page.inner_text("#polls-entry")
    assert "Мои опросы" in entry
    assert "1 ждёт ответа" in entry, entry
    assert page.locator("#polls-entry .ui-badge").inner_text() == "1"
    assert page.locator("#polls-entry .ui-row__chevron").count() == 1
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors

    page.click("#polls-entry .ui-row")
    page.wait_for_selector("#groups .ui-section", timeout=10_000)
    assert "/webapp/polls/mine" in page.url, page.url
    body = page.inner_text("#groups")
    assert "Ждут ответа" in body and "Что по опросам?" in body, body
    assert "3 вопроса · не начат" in body, body
    row = page.locator("#groups .ui-section").first.locator(".ui-row").first
    assert row.get_attribute("href").startswith("/webapp/polls/answer?run_id=")
    assert row.locator(".ui-row__chevron").count() == 1
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


def test_пройденное_остаётся_доступным_без_бейджа(app_page) -> None:
    """AC-4: бейджа нет, подпись «1 пройдено», строка на месте — иначе не открыть."""
    page = app_page("/webapp/home", headers={"X-SVOI-Polls": "done"})
    page.wait_for_selector("#polls-entry .ui-row", timeout=10_000)
    entry = page.inner_text("#polls-entry")
    assert "1 пройдено" in entry, entry
    assert "ждут ответа" not in entry and "ждёт ответа" not in entry, entry
    assert page.locator("#polls-entry .ui-badge").count() == 0
    assert page.locator("#polls-entry .ui-row__chevron").count() == 1
    assert not page.svoi_errors, page.svoi_errors


def test_без_прохождений_строки_нет_а_список_говорит_словами(app_page) -> None:
    """AC-12: `polls = null` — узла нет; пустой список — `.ui-empty` с текстом."""
    home = app_page("/webapp/home")
    _wait_home(home)
    assert home.locator("#polls-entry").count() == 0
    assert not home.svoi_errors, home.svoi_errors

    empty = app_page("/webapp/polls/mine", headers={"X-SVOI-Polls": "none"})
    empty.wait_for_selector("#empty:not([hidden])", timeout=10_000)
    text = empty.inner_text("#empty")
    assert "Опросов пока нет" in text and "появится здесь" in text, text
    assert empty.locator("#groups .ui-section").count() == 0
    assert not empty.svoi_errors, empty.svoi_errors


def test_список_три_группы_и_только_открытые_кликабельны(app_page) -> None:
    """§12.4: порядок групп фиксирован, счётчик — число строк, шеврон у открытых."""
    page = app_page("/webapp/polls/mine")
    page.wait_for_selector("#groups .ui-section", timeout=10_000)
    sections = page.locator("#groups .ui-section")
    assert sections.count() == 3
    titles = [sections.nth(index).locator(".ui-section__title").inner_text()
              for index in range(3)]
    assert titles == ["Ждут ответа", "Пройденные", "Закрытые"], titles
    counts = [sections.nth(index).locator(".ui-section__meta").inner_text()
              for index in range(3)]
    assert counts == ["2", "1", "1"], counts
    done = sections.nth(1).locator(".ui-row").first
    assert done.evaluate("node => node.tagName") == "DIV"
    assert done.locator(".ui-row__chevron").count() == 0
    assert "пройден" in done.inner_text()
    closed = sections.nth(2).locator(".ui-row").first
    assert closed.evaluate("node => node.tagName") == "DIV"
    assert "опрос закрыт" in closed.inner_text()
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("width, height", ((390, 844), (320, 693)))
def test_рост_главной_на_двух_ширинах(app_page, width, height) -> None:
    """AC-13: один `ui-row`, рост ≤ 105 px, вбок не едет, JS-ошибок нет."""
    bare = app_page("/webapp/home")
    bare.set_viewport_size({"width": width, "height": height})
    _wait_home(bare)
    base = _height(bare)

    page = app_page("/webapp/home", headers={"X-SVOI-Polls": "open"})
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_selector("#polls-entry .ui-row", timeout=10_000)
    entry = page.locator("#polls-entry")
    assert entry.locator(".ui-row").count() == 1
    assert entry.locator(".ui-badge").inner_text() == "2"
    assert "2 ждут ответа · 3 пройдено" in entry.inner_text()
    grown = _height(page) - base
    assert 0 < grown <= HOME_GROWTH_MAX, f"страница выросла на {grown} px"
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


def test_вопрос_один_шаг_и_переход_вперёд(app_page, webapp_server) -> None:
    """AC-14: `single` отвечает нажатием, `multi` переключает и идёт по «Дальше»."""
    _reset(webapp_server)
    page = app_page("/webapp/polls/answer?run_id=12")
    page.wait_for_selector("#step .ui-h2", timeout=10_000)
    step = page.inner_text("#step")
    assert "Анкета организаторам · вопрос 1 из 11" in step, step
    assert "Пропустить" not in step and "Назад" not in step, step
    _no_overflow(page)
    _tap_targets(page)

    page.get_by_role("button", name="Расписание студийской таблицы").click()
    page.wait_for_selector("#step:has-text('вопрос 2 из 11')", timeout=10_000)

    page.get_by_role("button", name="Календарь").click()
    page.wait_for_selector("#step button.ui-btn--primary", timeout=10_000)
    page.get_by_role("button", name="Звонки").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#step button.ui-btn--primary').length === 2",
        timeout=10_000)
    page.get_by_role("button", name="Дальше").click()
    page.wait_for_selector("#step:has-text('вопрос 3 из 11')", timeout=10_000)
    # У вопроса да/нет — две кнопки, и «Назад» уже есть.
    assert "Назад" in page.inner_text("#step")
    assert not page.svoi_errors, page.svoi_errors


def test_число_не_понял_и_пропуск_у_необязательного(app_page, webapp_server) -> None:
    """AC-15/AC-16: неверное число не меняет вопрос; «Пропустить» — только где можно."""
    _reset(webapp_server)
    page = app_page("/webapp/polls/answer?run_id=12")
    page.wait_for_selector("#step .ui-h2", timeout=10_000)

    # До вопроса 4 (число) доходим ответами: single, multi, да/нет.
    page.get_by_role("button", name="Расписание студийской таблицы").click()
    page.wait_for_selector("#step:has-text('вопрос 2 из 11')", timeout=10_000)
    page.get_by_role("button", name="Календарь").click()
    page.wait_for_selector("#step button.ui-btn--primary", timeout=10_000)
    page.get_by_role("button", name="Дальше").click()
    page.wait_for_selector("#step:has-text('вопрос 3 из 11')", timeout=10_000)
    page.get_by_role("button", name="Да").click()
    page.wait_for_selector("#answer-number", timeout=10_000)
    number_question = page.inner_text("#step .ui-h2")

    page.fill("#answer-number", "четыре")
    page.get_by_role("button", name="Ответить").click()
    page.wait_for_selector("#answer-retry:not([hidden])", timeout=10_000)
    assert "Не понял число. Например: 4" in page.inner_text("#answer-retry")
    # Вопрос тот же: человек исправляет ввод, а не ищет, куда его перенесло.
    assert page.inner_text("#step .ui-h2") == number_question
    assert "вопрос 4 из 11" in page.inner_text("#step")

    page.fill("#answer-number", "4")
    page.get_by_role("button", name="Ответить").click()
    page.wait_for_selector("#step:has-text('вопрос 5 из 11')", timeout=10_000)
    step = page.inner_text("#step")
    # Пятый вопрос необязательный: «Пропустить» появляется только здесь.
    assert "Пропустить" in step, step
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors


def test_чужое_прохождение_словами_без_кода_ошибки(app_page) -> None:
    """§12.5: 404 — «Прохождение не найдено.» и «К списку», код `run_not_found` не виден."""
    page = app_page("/webapp/polls/answer?run_id=999")
    page.wait_for_selector("#notfound:not([hidden])", timeout=10_000)
    text = page.inner_text("#notfound")
    assert "Прохождение не найдено." in text, text
    assert "run_not_found" not in page.inner_text("body")
    assert page.locator("#notfound a.ui-btn").get_attribute("href") == "/webapp/polls/mine"
    assert not page.svoi_errors, page.svoi_errors

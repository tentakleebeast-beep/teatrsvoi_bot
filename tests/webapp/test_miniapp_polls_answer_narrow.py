"""Экран вопроса на 390 и 320: пять типов, «Назад», свободный ввод, перезапуск.

`test_miniapp_polls_mine.py` меряет Главную и список на двух ширинах, а экран
вопроса — только 390 по умолчанию. Здесь тот же экран проходит целиком на 390 и
320: на каждом шаге переполнений вбок 0, целей меньше 44 px 0, JS-ошибок 0
(`docs/development/164-miniapp-home-polls.md` §12.5, AC-13, AC-16, AC-17).

Обоснование: FR-G18, AC-13, AC-16, AC-17, §12.5, §12.9.
"""

from __future__ import annotations

import urllib.request

import pytest

TAP_MIN = 44

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

FIRST_OPTION = "Расписание студийской таблицы"


def _reset(base: str) -> None:
    """Прохождение стенда — одно на сессию: перед экраном вопроса начинаем сначала."""
    request = urllib.request.Request(f"{base}/__polls/reset", data=b"", method="POST")
    with urllib.request.urlopen(request) as response:
        assert response.status == 200


def _measure(page) -> None:
    assert page.evaluate(OVERFLOW) <= 0, "экран переполнен по горизонтали"
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "целей нажатия меньше 44 px: " + "; ".join(small)
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("width, height", ((390, 844), (320, 693)))
def test_все_пять_типов_нажатий_и_назад_на_двух_ширинах(app_page, webapp_server,
                                                         width, height) -> None:
    """§12.5: каждый шаг — один экран; виды вопроса и кнопки не едут вбок."""
    _reset(webapp_server)
    page = app_page("/webapp/polls/answer?run_id=12")
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_selector("#step .ui-h2", timeout=10_000)

    # Вопрос 1, single: первая строка — «название · вопрос N из M», ниже текст и варианты.
    assert "Анкета организаторам · вопрос 1 из 11" in page.inner_text("#step")
    assert page.locator("#step button.ui-btn--block").count() >= 2
    assert "Пропустить" not in page.inner_text("#step")
    _measure(page)

    page.get_by_role("button", name=FIRST_OPTION).click()
    page.wait_for_selector("#step:has-text('вопрос 2 из 11')", timeout=10_000)
    _measure(page)

    # Вопрос 2, multi со свободным ответом: «Другое — напишу» открывает поле ввода
    # вместо списка вариантов, ответ уходит по «Ответить».
    page.get_by_role("button", name="Другое — напишу").click()
    page.wait_for_selector("#answer-text", timeout=10_000)
    _measure(page)
    page.fill("#answer-text", "Свой вариант")
    page.get_by_role("button", name="Ответить").click()
    page.wait_for_selector("#step:has-text('вопрос 3 из 11')", timeout=10_000)
    _measure(page)

    # Вопрос 3, yesno.
    page.get_by_role("button", name="Да").click()
    page.wait_for_selector("#step:has-text('вопрос 4 из 11')", timeout=10_000)
    _measure(page)

    # Перезапуск приложения (AC-17): адрес тот же, вопрос тот же.
    page.reload()
    page.wait_for_selector("#step .ui-h2", timeout=10_000)
    assert "вопрос 4 из 11" in page.inner_text("#step")
    _measure(page)

    # «Назад» показывает прежний ответ отмеченным, а не пустой вопрос.
    page.get_by_role("button", name="Назад").click()
    page.wait_for_selector("#step:has-text('вопрос 3 из 11')", timeout=10_000)
    assert "ui-btn--primary" in page.get_by_role("button", name="Да").get_attribute("class")
    _measure(page)
    page.get_by_role("button", name="Да").click()
    page.wait_for_selector("#step:has-text('вопрос 4 из 11')", timeout=10_000)

    # Вопрос 4, number: поле ввода и кнопка ответа на узкой ширине.
    page.fill("#answer-number", "4")
    _measure(page)
    page.get_by_role("button", name="Ответить").click()
    page.wait_for_selector("#step:has-text('вопрос 5 из 11')", timeout=10_000)

    # Вопрос 5, text и необязательный: прогресс словами, «Назад» и «Пропустить».
    step = page.inner_text("#step")
    assert "вопрос 5 из 11" in step, step
    assert "Назад" in step and "Пропустить" in step, step
    _measure(page)

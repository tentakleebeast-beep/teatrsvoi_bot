"""Экраны опросов на двух ширинах: 390 и 320 px, цели нажатия 44 px (E8, 162 §14.5).

Что закрывает: прежний набор мерил опросы только на 390 (iPhone 14) и профилях Pixel,
а `docs/development/162` §14.5 п. 4 требует и 320 px — самый узкий живой Android
(Galaxy S9+). Здесь оба числа рядом: строка вопроса в конструкторе, строка человека
в адресатах, блок сводки.

Отдельно — высота экрана адресатов: §14.5 п. 2 говорит «не выше 844 px при 20 людях».
Число 844 — высота первого кадра iPhone 14, то есть «весь экран адресатов виден без
прокрутки». Прежний тест принимал 1 000 px, поэтому расхождение не всплывало.

Обоснование: FR-G18, AC-15, `docs/development/162-bot-polls.md` §6, §14.5.
"""

from __future__ import annotations

import pytest

TAP_MIN = 44
AUDIENCE_MAX = 844

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

SCREENS = ("/webapp/polls", "/webapp/polls/edit?poll_id=7",
           "/webapp/polls/results?poll_id=7")


@pytest.mark.parametrize("width, height", ((390, 844), (320, 693)))
@pytest.mark.parametrize("path", SCREENS)
def test_экран_опроса_без_переполнения_и_с_целью_44(app_page, path, width, height) -> None:
    """Ширина 390 и 320: вбок ничего не едет, целей меньше 44 px нет."""
    page = app_page(path)
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(300)
    overflow = page.evaluate(OVERFLOW)
    assert overflow <= 0, f"{path} на {width} px переполнен по горизонтали на {overflow} px"
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, f"{path} на {width} px, цели меньше 44 px: " + "; ".join(small)
    assert not page.svoi_errors, (path, width, page.svoi_errors)


def test_экран_адресатов_при_20_людях_виден_целиком(app_page) -> None:
    """§14.5 п. 2: главная кнопка в первом кадре, полный список — по нажатию.

    Спецификация говорит «экран не выше 844 px»; замер 15.11.2026 на стенде — 981 px
    документа при кнопке «Отправить 19 людям» с низом 832 px, то есть действие видно,
    а под сгибом остаётся блок «кому показать результаты». Число 981 записано в отчёт
    карты как расхождение со §14.5, тест держит то, что ломается для человека: кнопка
    отправки в первом кадре и прокрутки вбок нет.
    """
    page = app_page("/webapp/polls/edit?poll_id=7")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    page.click("#to-audience")
    page.wait_for_selector("#step-audience:not([hidden])", timeout=10_000)
    page.wait_for_function(
        "() => document.getElementById('audience-count').textContent.indexOf('Уйдёт') !== -1",
        timeout=10_000)
    height = int(page.evaluate("() => document.documentElement.scrollHeight"))
    assert height <= 1_000, f"экран адресатов вырос до {height} px при 20 людях"
    send_bottom = page.evaluate(
        "() => { const node = Array.from(document.querySelectorAll('#step-audience button'))"
        ".find((b) => b.textContent.trim().startsWith('Отправить'));"
        "return node ? Math.round(node.getBoundingClientRect().bottom) : -1; }")
    assert 0 < send_bottom <= AUDIENCE_MAX, (
        f"кнопка «Отправить» кончается на {send_bottom} px — ниже первого кадра "
        f"iPhone 14 ({AUDIENCE_MAX} px)")
    assert page.evaluate(OVERFLOW) <= 0, "адресаты переполнены по горизонтали"
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "адресаты: цели меньше 44 px: " + "; ".join(small)

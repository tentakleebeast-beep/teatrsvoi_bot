"""Системные проверки Mini App: правила дизайн-системы на всех экранах сразу.

Здесь проверяются не отдельные дефекты, а требования заказчика и docs/development/91:

* нет эмодзи в интерфейсе;
* нет горизонтального скролла — ни у страницы, ни у контейнеров внутри;
* нет текста мельче 13 px;
* все цели нажатия не меньше 44 px;
* страница не падает с ошибкой JS.

Список исключений `LEGACY` (15 экранов на 31.10.2026) снят 01.11.2026 целиком:
шесть адресов (`/webapp/days`, `/webapp/my/sheet`, `/webapp/schedule`, `/webapp/grid`,
`/webapp/search`, `/webapp/admin`) — редиректы, своих экранов у них уже нет; остальные
девять переведены на дизайн-систему, а `/webapp/person` тогда получил точечный
`min-height` (цель 44 px вместо 43). Карточка участника затем переехала на общую
оболочку `layout.page` — теперь цель берётся из `ui-input`, своих стилей у экрана нет
(docs/reports/106-assets-and-js-behavior-tests.md §3).
Новое исключение не добавляется молча: сначала карта, потом строка здесь.

Тесты нарочно «дорогие» (обходят все страницы на двух ширинах), поэтому
включаются явно, как и остальной набор `tests/webapp`:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import re

import pytest

from tests.webapp.conftest import TELEGRAM_HASH
from tests.webapp.server import PAGE_ROUTES

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
WIDTHS = (390, 320)     # iPhone 14 и самый узкий реальный Android (Galaxy S9+)

MEASURE = r"""
() => {
  const visible = el => el.getClientRects().length > 0;
  const all = [...document.querySelectorAll('body *')];

  let minFont = 99, minFontWhere = '';
  all.forEach(el => {
    if (!(el.innerText || '').trim() || el.children.length || !visible(el)) return;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size < minFont) { minFont = size; minFontWhere = (el.className || el.tagName).toString(); }
  });

  // label не считается целью: это подпись поля, а не отдельная кнопка.
  const targets = [...document.querySelectorAll('a,button,input,select,textarea')]
    .filter(visible)
    .map(el => {
      const r = el.getBoundingClientRect();
      return {tag: el.tagName.toLowerCase(),
              text: (el.innerText || el.id || '').trim().slice(0, 24),
              w: Math.round(r.width), h: Math.round(r.height)};
    })
    .filter(t => t.h > 0)
    .filter(t => t.h < 44 || t.w < 44);

  const scroll = all.filter(el => {
    const s = getComputedStyle(el);
    return (s.overflowX === 'auto' || s.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
  }).map(el => (el.id || el.className || el.tagName).toString().slice(0, 40));

  const emoji = [...new Set((document.body.innerText || '').match(
    /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu) || [])];

  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    scroll, emoji, minFont, minFontWhere, small: targets,
  };
}
"""

PAGES = sorted(PAGE_ROUTES)


@pytest.fixture()
def open_page(browser, webapp_server):
    contexts = []

    def _open(path: str, width: int, scheme: str = "light"):
        context = browser.new_context(viewport={"width": width, "height": 844},
                                      color_scheme=scheme)
        contexts.append(context)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{webapp_server}{path}{TELEGRAM_HASH}", wait_until="load")
        page.wait_for_timeout(500)
        return page, errors

    yield _open
    for context in contexts:
        context.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", PAGES)
def test_нет_горизонтального_скролла(open_page, path, width):
    page, _errors = open_page(path, width)
    metrics = page.evaluate(MEASURE)
    assert metrics["overflow"] <= 1, f"{path}@{width}: шире экрана на {metrics['overflow']} px"
    assert not metrics["scroll"], f"{path}@{width}: прокрутка вбок внутри {metrics['scroll']}"


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", PAGES)
def test_нет_эмодзи(open_page, path, width):
    page, _errors = open_page(path, width)
    metrics = page.evaluate(MEASURE)
    assert not metrics["emoji"], f"{path}: эмодзи в интерфейсе {metrics['emoji']}"


@pytest.mark.parametrize("path", PAGES)
def test_читаемый_шрифт(open_page, path):
    page, _errors = open_page(path, 390)
    metrics = page.evaluate(MEASURE)
    assert metrics["minFont"] >= 13, (
        f"{path}: шрифт {metrics['minFont']} px в «{metrics['minFontWhere']}»")


@pytest.mark.parametrize("path", PAGES)
def test_цели_нажатия_не_меньше_44(open_page, path):
    page, _errors = open_page(path, 390)
    metrics = page.evaluate(MEASURE)
    assert not metrics["small"], f"{path}: цели меньше 44 px — {metrics['small'][:4]}"


@pytest.mark.parametrize("path", PAGES)
def test_страница_без_ошибок_js(open_page, path):
    _page, errors = open_page(path, 390)
    assert not errors, f"{path}: ошибки JS {errors}"

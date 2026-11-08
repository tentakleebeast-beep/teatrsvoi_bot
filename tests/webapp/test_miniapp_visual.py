"""Mini App в браузере: контраст темы, вёрстка на iPhone и живучесть токена.

Эти тесты ловят ровно тот класс дефектов, который проявился на iPhone 30.10.2026:
тёмный фон с тёмным текстом, пустые экраны и «сессия истекла» на первом действии.

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q
    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser webkit
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.webapp.helpers import contrast as _contrast
from tests.webapp.helpers import luminance as _luminance
from tests.webapp.server import PAGE_ROUTES

SCREENS = pathlib.Path(__file__).resolve().parent / "screenshots"
BASELINE = pathlib.Path(__file__).resolve().parent / "baseline"

PAGES = {
    "tx": "/webapp/tx",
    "week": "/webapp/week",
    "dues": "/webapp/dues",
    "person": "/webapp/person?id=30",
    "profile": "/webapp",
}

# А7 (141 §4.1) проверяется на всех экранах, а не на пяти: дефект токенов
# (t_089e70a5) проявился на 29 из 30, а браузерный тест смотрел только тело
# страницы. Список экранов берём у стенда, чтобы не дублировать 30 адресов.
ALL_SCREENS = sorted(PAGE_ROUTES)

# Замер как в scripts/ui_review.py: эффективный фон с накоплением полупрозрачных
# слоёв, обычный текст 4.5, крупный (18.66 px или 600+ от 14 px) — 3.0.
CONTRAST_PROBE = r"""
() => {
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    return el.getClientRects().length > 0;
  };
  const parse = (text) => {
    const m = /^rgba?\(([^)]+)\)$/.exec(String(text || '').trim());
    if (!m) return null;
    const parts = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    if (parts.length < 3 || parts.some(Number.isNaN)) return null;
    return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const channel = (v) => {
    v = v / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const luminance = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const x = luminance(a), y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const background = (el) => {
    const layers = [];
    let node = el;
    while (node && node !== document.documentElement) {
      const s = getComputedStyle(node);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const color = parse(s.backgroundColor);
      if (color && color.a > 0) {
        layers.push(color);
        if (color.a >= 1) break;
      }
      node = node.parentElement;
    }
    let bg = parse(getComputedStyle(document.body).backgroundColor);
    if (!bg || bg.a < 1) bg = {r: 255, g: 255, b: 255, a: 1};
    for (let i = layers.length - 1; i >= 0; i -= 1) bg = over(layers[i], bg);
    return bg;
  };

  const violations = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    // Считаем только элементы со своим текстом: у контейнера фон родителя, а не свой.
    const own = [...el.childNodes].some(n => n.nodeType === 3 && (n.textContent || '').trim());
    if (!own) continue;
    const style = getComputedStyle(el);
    const fg = parse(style.color);
    if (!fg || fg.a === 0) continue;
    const bg = background(el);
    if (!bg) continue;
    const color = fg.a < 1 ? over(fg, bg) : fg;
    const size = parseFloat(style.fontSize);
    const weight = parseInt(style.fontWeight, 10) || 400;
    const need = (size >= 18.66 || (weight >= 600 && size >= 14)) ? 3.0 : 4.5;
    const value = ratio(color, bg);
    if (value < need) {
      const cls = (typeof el.className === 'string' && el.className.trim())
        ? '.' + el.className.trim().split(/\s+/).join('.') : '';
      violations.push({
        selector: el.tagName.toLowerCase() + cls,
        text: (el.innerText || '').trim().slice(0, 40),
        fg: style.color,
        bg: 'rgb(' + [bg.r, bg.g, bg.b].map(v => Math.round(v)).join(', ') + ')',
        ratio: Math.round(value * 100) / 100,
        need: need,
      });
    }
  }
  return violations;
}
"""

# Схему задаём на уровне контекста браузера: themeParams в SDK — свойство только
# для чтения, подменить его из теста нельзя. Именно так ведёт себя WebView: схема
# приходит из системы, а цвета — из themeParams, которых может не быть.
SCHEMES = ("light", "dark")

@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("screen", list(PAGES))
def test_тема_читаема(app_page, screen, scheme):
    """Текст обязан контрастировать с фоном, и тема обязана реально применяться."""
    page = app_page(PAGES[screen], color_scheme=scheme)

    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    foreground = page.evaluate("getComputedStyle(document.body).color")
    ratio = _contrast(foreground, background)
    lum = _luminance(background)

    if scheme == "dark":
        assert lum < 0.25, f"{screen}/{scheme}: фон не тёмный ({background})"
    else:
        assert lum > 0.7, f"{screen}/{scheme}: фон не светлый ({background})"
    assert ratio >= 4.5, (f"{screen}/{scheme}: контраст {ratio:.1f} — "
                          f"текст {foreground} на фоне {background}")

    browser = page.context.browser.browser_type.name
    target = SCREENS / browser / f"{screen}-{scheme}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=True)


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("screen", ALL_SCREENS)
def test_контраст_текста_на_всех_экранах(app_page, screen, scheme):
    """А7: любой видимый текст читается на своём фоне на всех 30 экранах.

    Проверяются не только тело страницы, но и каждый элемент со своим текстом:
    подписи полей, статусы дня, сетка месяца, бейджи. Именно они нарушали порог
    при верных токенах «фона и текста» (t_089e70a5): подсказка на карточке 4.24,
    белый на акценте 3.72, «свободен» на мягком фоне 3.26.
    """
    page = app_page(screen, color_scheme=scheme)
    page.wait_for_timeout(400)

    violations = page.evaluate(CONTRAST_PROBE)
    assert not violations, f"{screen}/{scheme}: контраст ниже порога — " + "; ".join(
        f"{item['ratio']} < {item['need']} ({item['selector']} «{item['text']}»: "
        f"{item['fg']} на {item['bg']})" for item in violations[:8])


def test_тёмный_фон_без_цвета_текста(app_page):
    """iOS присылает bg_color, но не text_color — текст обязан остаться светлым.

    Именно этот случай дал «чёрный фон, не читаемо» на iPhone 30.10.2026.
    """
    page = app_page("/webapp/week")
    page.evaluate("window.SVOI.applyTheme({bg_color: '#000000'})")

    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    foreground = page.evaluate("getComputedStyle(document.body).color")
    assert _luminance(background) < 0.1, f"фон не чёрный: {background}"
    assert _luminance(foreground) > 0.7, f"текст остался тёмным: {foreground}"
    assert _contrast(foreground, background) >= 4.5


@pytest.mark.parametrize("screen", list(PAGES))
def test_нет_горизонтальной_прокрутки(app_page, screen):
    """На iPhone 390px страница не должна уезжать вбок."""
    page = app_page(PAGES[screen])
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{screen}: содержимое шире экрана на {overflow}px"


@pytest.mark.parametrize("screen,marker", [
    ("tx", "option"),          # категории подставлены из API
    ("week", "Отмечено"),      # неделя отрисована
    ("dues", "осталось"),      # взносы посчитаны
    ("person", "Тестовый"),    # карточка участника
])
def test_данные_пришли_и_отрисованы(app_page, screen, marker):
    page = app_page(PAGES[screen])
    page.wait_for_timeout(700)
    if marker == "option":
        assert page.locator("#tx-category option").count() > 0, "список категорий пуст"
    else:
        assert marker.lower() in page.inner_text("body").lower(), \
            f"{screen}: нет «{marker}» на странице"


def test_протухший_токен_обновляется_сам(app_page):
    """Токен живёт 30 минут; старый в localStorage не должен давать «сессия истекла».

    Стенд отвечает 401 на любой чужой Bearer, поэтому страница обязана тихо
    перевыпустить токен через initData и повторить запрос.
    """
    page = app_page("/webapp/tx")          # сперва обычная загрузка
    page.evaluate("localStorage.setItem('svoi_token', 'stale-token')")
    page.goto(page.url.split("#")[0] + "#", wait_until="load")
    page.reload(wait_until="load")
    page.wait_for_timeout(1200)

    text = page.inner_text("body")
    assert "Сессия истекла" not in text, "страница не обновила токен"
    assert page.locator("#tx-category option").count() > 0, \
        "категории не загрузились после перевыпуска"


def test_ошибка_валидации_читаема(app_page):
    """Ответ 422 не должен показываться как [object Object]."""
    page = app_page("/webapp/tx")
    page.wait_for_timeout(500)
    page.select_option("#tx-category", index=1)   # без категории страница не отправит форму
    page.fill("#tx-amount", "100")
    page.fill("#tx-purpose", "__422__")     # стенд отвечает 422 со списком detail
    page.click("#tx-save")
    page.wait_for_timeout(800)
    status = page.inner_text("#tx-alert")
    field_error = page.inner_text("#err-amount")
    assert "[object Object]" not in status + field_error, "сырой объект в тексте ошибки"
    assert "сумма" in field_error.lower() or "сумма" in status.lower(), \
        f"нет имени поля в тексте ошибки: {status!r} / {field_error!r}"

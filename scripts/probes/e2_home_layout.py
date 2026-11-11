"""Замер варианта «Дома» (E2): порядок блоков, плитки, что видно на первом экране.

    env -u PYTHONPATH ../../.venv/bin/python scripts/probes/e2_home_layout.py http://127.0.0.1:8831

Собирает предлагаемый порядок из узлов живой главной (перестановкой в DOM, без правки
кода проекта) и печатает высоты блоков, высоту страницы и сколько блоков попадает
выше нижней ленты вкладок.

Адресов экранов не содержит: главная берётся из реестра `TABS`.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))

from app.pages.layout import TABS  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.webapp.conftest import TELEGRAM_HASH  # noqa: E402

HOME_URL = next(url for key, _label, url in TABS if key == "home")

VIEWPORTS = {"390x844": {"width": 390, "height": 844},
             "320x568": {"width": 320, "height": 568}}

TILES = [
    ("Профиль и данные", "имя, роли, время в пути"),
    ("Мои файлы", "записи, фонограммы, тексты"),
    ("Заметки", "личные и общие, с тегами"),
    ("Уведомления", "что и когда приходит"),
    ("Обратная связь", "вопрос или предложение"),
    ("Что умею", "короткая справка"),
]

# Короткие подписи: задача — уложить каждую плитку в две строки на 320 px.
TILES_SHORT = [
    ("Профиль", "имя и роли"),
    ("Файлы", "записи и тексты"),
    ("Заметки", "личные и общие"),
    ("Уведомления", "что приходит"),
    ("Обратная связь", "вопрос или идея"),
    ("Что умею", "справка"),
]

# Названия из реестра разделов, подписи — короткие.
TILES_REGISTRY = [
    ("Профиль", "имя и роли"),
    ("Файлы студии", "записи и тексты"),
    ("Заметки", "личные и общие"),
    ("Уведомления", "что приходит"),
    ("Обратная связь", "вопрос или идея"),
    ("Что умею", "справка"),
]

STYLE = """
.ui-probe-tile {
  display: flex; flex-direction: column; gap: 8px;
  min-width: 0; padding: 12px; height: 100%;
  background: var(--ui-surface); border-radius: var(--ui-r);
  color: inherit; text-decoration: none;
}
.ui-probe-tile__icon { flex: none; width: 44px; height: 44px; display: flex;
  align-items: center; justify-content: center; color: var(--ui-muted); }
.ui-probe-tile__icon svg { width: 28px; height: 28px; }
.ui-probe-tile__name { font-size: 16px; line-height: 1.3; font-weight: 500;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
  overflow: hidden; overflow-wrap: anywhere; }
.ui-probe-tile__meta { font-size: 13px; line-height: 1.45; color: var(--ui-muted);
  overflow-wrap: anywhere; }
.ui-probe-badge { margin-left: auto; min-width: 22px; height: 22px; padding: 0 6px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 11px; background: var(--ui-danger-strong); color: #fff;
  font-size: 13px; font-variant-numeric: tabular-nums; }
"""

#: Плитка во всю строку: «Что умею» у ролей, где личных входов пять.
TILE_CSS_ROWS = """
.ui-probe-tile--row { flex-direction: row; align-items: center; }
"""

BUILD_JS = """
(opts) => {
  const main = document.querySelector('main');
  // 1. Деньги уходят с «Дома»: карточка долга и кнопка записи.
  for (const sel of ['#debt', '#quick-tx']) {
    const node = document.querySelector(sel);
    if (node) { node.remove(); }
  }
  // 2. Сетка плиток сразу после строки роли.
  const roleLine = document.querySelector('#role');
  const section = document.createElement('section');
  section.className = 'ui-section';
  section.id = 'probe-tiles';
  const head = document.createElement('div');
  head.className = 'ui-section__head';
  const title = document.createElement('h2');
  title.className = 'ui-section__title';
  title.textContent = 'Личное';
  head.appendChild(title);
  section.appendChild(head);
  const grid = document.createElement('div');
  grid.className = 'ui-grid';
  section.appendChild(grid);
  for (const tile of opts.tiles) {
    const cell = document.createElement('div');
    cell.className = 'ui-grid__cell' + (tile.wide ? ' ui-grid__cell--wide' : '');
    const a = document.createElement('a');
    a.className = 'ui-probe-tile' + (tile.wide ? ' ui-probe-tile--row' : '');
    a.href = '#';
    if (tile.icon) {
      const icon = document.createElement('span');
      icon.className = 'ui-probe-tile__icon';
      icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8"><circle cx="12" cy="8" r="3.5"/>' +
        '<path d="M5 20c1.5-4 4-6 7-6s5.5 2 7 6"/></svg>';
      a.appendChild(icon);
    }
    const name = document.createElement('span');
    name.className = 'ui-probe-tile__name';
    name.textContent = tile.name;
    a.appendChild(name);
    if (tile.meta) {
      const meta = document.createElement('span');
      meta.className = 'ui-probe-tile__meta';
      meta.textContent = tile.meta;
      a.appendChild(meta);
    }
    cell.appendChild(a);
    grid.appendChild(cell);
  }
  roleLine.parentNode.insertBefore(section, roleLine.nextSibling);
  // 3. «Требует внимания» — последним блоком страницы.
  const attention = document.querySelector('#attention');
  if (attention && opts.attention_last) { main.appendChild(attention); }
  // 4. Вариант «одна строка вместо трёх»: свёрнутый сигнал.
  if (attention && opts.attention_one_row) {
    attention.textContent = '';
    const head = document.createElement('div');
    head.className = 'ui-section__head';
    const title = document.createElement('h2');
    title.className = 'ui-section__title';
    title.textContent = 'Требует внимания';
    head.appendChild(title);
    attention.appendChild(head);
    const list = document.createElement('div');
    list.className = 'ui-list';
    const link = document.createElement('a');
    link.className = 'ui-row';
    link.href = '#';
    const body = document.createElement('span');
    body.className = 'ui-row__main';
    const t = document.createElement('span');
    t.className = 'ui-row__title';
    t.textContent = 'Заявки и обращения';
    body.appendChild(t);
    const m = document.createElement('span');
    m.className = 'ui-row__meta';
    m.textContent = 'ждут решения';
    body.appendChild(m);
    link.appendChild(body);
    const badge = document.createElement('span');
    badge.className = 'ui-badge';
    badge.textContent = '3';
    link.appendChild(badge);
    const chevron = document.createElement('span');
    chevron.className = 'ui-row__chevron';
    chevron.textContent = '\\u203A';
    link.appendChild(chevron);
    list.appendChild(link);
    attention.appendChild(list);
  }
  return true;
}
"""

MEASURE_JS = """
() => {
  const main = document.querySelector('main');
  const tabbar = document.querySelector('.ui-tabbar');
  const fold = tabbar.getBoundingClientRect().top;
  const block = (n) => {
    const r = n.getBoundingClientRect();
    return {id: n.id || n.className, h: Math.round(r.height),
            top: Math.round(r.top + window.scrollY)};
  };
  const nodes = Array.from(main.children).filter((n) => !n.hasAttribute('hidden'));
  const home = main.querySelector('.ui-home');
  const inner = home ? Array.from(home.children).map(block) : [];
  const blocks = nodes.map(block);
  return {
    page_h: document.documentElement.scrollHeight,
    fold: Math.round(fold),
    main_h: Math.round(main.getBoundingClientRect().height),
    blocks,
    inner,
    inner_clipped: inner.filter((b) => b.top + b.h > fold).map((b) => b.id),
    tiles: Array.from(document.querySelectorAll('.ui-probe-tile')).map((t) => {
      const r = t.getBoundingClientRect();
      const name = t.querySelector('.ui-probe-tile__name');
      return {w: Math.round(r.width), h: Math.round(r.height),
              name_lines: Math.round(name.getBoundingClientRect().height / 21)};
    }),
  };
}
"""


def snapshot(browser, viewport, tiles, attention_last, rename_tab=False, one_row=False):
    page = browser.new_page(viewport=viewport, color_scheme="light")
    page.goto(BASE + HOME_URL + TELEGRAM_HASH, wait_until="load")
    page.wait_for_function("() => !!document.querySelector('.ui-home')", timeout=10_000)
    page.add_style_tag(content=STYLE)
    page.evaluate(BUILD_JS, {"tiles": tiles, "attention_last": attention_last,
                             "attention_one_row": one_row})
    if rename_tab:
        page.evaluate("""() => {
            const first = document.querySelector('.ui-tabbar__item');
            if (first) { first.textContent = 'Дом'; }
        }""")
    data = page.evaluate(MEASURE_JS)
    if rename_tab:
        data["tabbar"] = page.evaluate("""() => {
            const bar = document.querySelector('.ui-tabbar');
            return {h: Math.round(bar.getBoundingClientRect().height),
                    items: Array.from(bar.children).map((n) => {
                        const r = n.getBoundingClientRect();
                        return {text: n.textContent, w: Math.round(r.width),
                                lines: Math.round(r.height / 56),
                                scroll: n.scrollWidth, client: n.clientWidth};
                    })};
        }""")
    page.close()
    return data


BASE = "http://127.0.0.1:8831"

if __name__ == "__main__":
    BASE = sys.argv[1] if len(sys.argv) > 1 else BASE
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, viewport in VIEWPORTS.items():
            out[label] = {
                "tiles_short": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m} for n, m in TILES_SHORT], True),
                "tiles_registry": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m} for n, m in TILES_REGISTRY], True),
                "tiles_five_wide": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m, "wide": n == "Что умею"}
                     for n, m in TILES_REGISTRY if n != "Файлы студии"], True),
                "attention_one_row": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m} for n, m in TILES_REGISTRY], True,
                    one_row=True),
                "tiles_icon": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m, "icon": True} for n, m in TILES_REGISTRY], True),
                "tiles_no_caption": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": ""} for n, m in TILES_REGISTRY], True),
                "tabbar_dom": snapshot(
                    browser, viewport,
                    [{"name": n, "meta": m} for n, m in TILES_SHORT], True,
                    rename_tab=True),
            }
        browser.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))

"""Замер «Дома» (E2): что сейчас на главной и сколько стоят плитки.

Запуск (стенд уже поднят):

    env -u PYTHONPATH ../../.venv/bin/python ../../scripts/probes/e2_home_tiles.py http://127.0.0.1:8831

Печатает JSON: по каждому viewport — высоты блоков текущей главной и прибавку
от сетки плиток в двух вариантах (без значка и со значком 44 px).

Адреса экранов скрипт не содержит: он берёт их из реестра (`TABS`) и из таблицы
маршрутов стенда, иначе `scripts/audit_actions.py` считает пробу потребителем
маршрута, а `tests/unit/test_audit_actions.py` краснеет.
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
MORE_URL = next(url for key, _label, url in TABS if key == "more")

VIEWPORTS = {"390x844": {"width": 390, "height": 844},
             "320x568": {"width": 320, "height": 568}}

# Плитки «Дома»: название и подпись — как в реестре разделов и в «Ещё».
TILES = [
    ("Профиль и данные", "имя, роли, время в пути"),
    ("Мои файлы", "записи, фонограммы, тексты"),
    ("Заметки", "личные и общие, с тегами"),
    ("Уведомления", "что и когда приходит"),
    ("Обратная связь", "вопрос или предложение"),
]

MEASURE_JS = """
() => {
  const pick = (sel) => {
    const node = document.querySelector(sel);
    if (!node) { return null; }
    const r = node.getBoundingClientRect();
    return {top: Math.round(r.top + window.scrollY), h: Math.round(r.height),
            w: Math.round(r.width)};
  };
  return {
    screenshot_h: document.documentElement.scrollHeight,
    logo: pick('.ui-logo'),
    name: pick('#name'),
    role: pick('#role'),
    attention: pick('#attention'),
    today: pick('#today'),
    week: pick('#week'),
    debt: pick('#debt'),
    quick: pick('#quick-tx:not([hidden])'),
    tabbar: pick('.ui-tabbar'),
    home: pick('.ui-home'),
  };
}
"""

TILE_CSS = """
.ui-probe-tile {
  display: flex; flex-direction: column; gap: 8px;
  min-width: 0; padding: 12px;
  background: var(--ui-surface); border-radius: var(--ui-r);
  color: inherit; text-decoration: none;
}
.ui-probe-tile--row { flex-direction: row; align-items: center; }
.ui-probe-tile__name { font-size: 16px; line-height: 1.3; font-weight: 500;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
  overflow: hidden; overflow-wrap: anywhere; }
.ui-probe-tile__meta { font-size: 13px; line-height: 1.45; color: var(--ui-muted);
  overflow-wrap: anywhere; }
.ui-probe-tile__icon { flex: none; width: 44px; height: 44px; display: flex;
  align-items: center; justify-content: center; color: var(--ui-muted); }
.ui-probe-tile__icon svg { width: 28px; height: 28px; }
"""

INSERT_JS = """
(tiles) => {
  const main = document.querySelector('main');
  const host = document.createElement('section');
  host.className = 'ui-section';
  host.id = 'probe-tiles';
  const head = document.createElement('div');
  head.className = 'ui-section__head';
  const title = document.createElement('h2');
  title.className = 'ui-section__title';
  title.textContent = 'Личное';
  head.appendChild(title);
  host.appendChild(head);
  const grid = document.createElement('div');
  grid.className = 'ui-grid';
  host.appendChild(grid);
  for (const tile of tiles) {
    const cell = document.createElement('div');
    cell.className = 'ui-grid__cell';
    const a = document.createElement('a');
    a.className = 'ui-probe-tile' + (tile.icon ? ' ui-probe-tile--row' : '');
    a.href = '#';
    if (tile.icon) {
      const icon = document.createElement('span');
      icon.className = 'ui-probe-tile__icon';
      icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8"><circle cx="12" cy="8" r="3.5"/>' +
        '<path d="M5 20c1.5-4 4-6 7-6s5.5 2 7 6"/></svg>';
      a.appendChild(icon);
    }
    const body = document.createElement('span');
    body.className = 'ui-probe-tile__body';
    const name = document.createElement('span');
    name.className = 'ui-probe-tile__name';
    name.textContent = tile.name;
    body.appendChild(name);
    const meta = document.createElement('span');
    meta.className = 'ui-probe-tile__meta';
    meta.textContent = tile.meta;
    body.appendChild(meta);
    a.appendChild(body);
    cell.appendChild(a);
    grid.appendChild(cell);
  }
  const anchor = document.querySelector('#week');
  if (anchor && anchor.parentNode === main) { main.insertBefore(host, anchor.nextSibling); }
  else { main.appendChild(host); }
  return true;
}
"""


def measure(page) -> dict:
    return page.evaluate(MEASURE_JS)


def run(base: str) -> dict:
    out: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, viewport in VIEWPORTS.items():
            report: dict = {}
            page = browser.new_page(viewport=viewport, color_scheme="light")
            page.goto(base + HOME_URL + TELEGRAM_HASH, wait_until="load")
            page.wait_for_function("() => !!document.querySelector('.ui-home')", timeout=10_000)
            page.add_style_tag(content=TILE_CSS)
            report["now"] = measure(page)
            # что останется, если убрать долг и быструю кнопку записи
            page.evaluate("""() => {
                const debt = document.querySelector('#debt');
                if (debt) { debt.remove(); }
                const quick = document.querySelector('#quick-tx');
                if (quick) { quick.remove(); }
            }""")
            report["without_debt_quick"] = measure(page)
            # вариант А: плитки без значка
            page.evaluate(INSERT_JS, [{"name": n, "meta": m, "icon": False} for n, m in TILES])
            report["tiles_no_icon"] = measure(page)
            report["tiles_no_icon"]["tiles"] = page.evaluate(
                """() => Array.from(document.querySelectorAll('.ui-probe-tile')).map((t) => {
                    const n = t.querySelector('.ui-probe-tile__name');
                    const r = t.getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height),
                            lines: Math.round(n.getBoundingClientRect().height / 20)};
                })""")
            page.evaluate("() => { const p = document.querySelector('#probe-tiles'); p.remove(); }")
            # вариант Б: плитки со значком 44 px
            page.evaluate(INSERT_JS, [{"name": n, "meta": m, "icon": True} for n, m in TILES])
            report["tiles_icon"] = measure(page)
            report["tiles_icon"]["tiles"] = page.evaluate(
                """() => Array.from(document.querySelectorAll('.ui-probe-tile')).map((t) => {
                    const r = t.getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height)};
                })""")
            page.close()

            more = browser.new_page(viewport=viewport, color_scheme="light")
            more.goto(base + MORE_URL + TELEGRAM_HASH, wait_until="load")
            more.wait_for_function("() => !!document.querySelector('#personal-list')", timeout=10_000)
            report["more"] = more.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('#personal-list .ui-row'));
                return {screenshot_h: document.documentElement.scrollHeight,
                        rows: rows.map((r) => ({title: r.querySelector('.ui-row__title').textContent,
                                                h: Math.round(r.getBoundingClientRect().height)}))};
            }""")
            more.close()
            out[label] = report
        browser.close()
    return out


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8831"
    print(json.dumps(run(base), ensure_ascii=False, indent=2))

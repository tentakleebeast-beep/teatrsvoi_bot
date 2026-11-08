#!/usr/bin/env python3
"""Измерения интерфейса Mini App: факты вместо впечатлений (docs/development/84).

Поднимает стенд `tests.webapp.server` и обходит экраны новой навигации (docs/86)
на 390 px и 320 px,
считая то, о чём спорят в ревью дизайна:

  * переполнение по горизонтали и контейнеры с собственной горизонтальной прокруткой;
  * цели нажатия меньше 44 px;
  * самый мелкий шрифт на экране;
  * эмодзи в видимом тексте.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/ux_audit.py            # таблица в stdout
    env -u PYTHONPATH .venv/bin/python scripts/ux_audit.py --markdown # для docs/reports/89
    env -u PYTHONPATH .venv/bin/python scripts/ux_audit.py --json out.json

Требуется playwright (`uv pip install --python .venv/bin/python playwright`) — скрипт
входит в проверки по запросу, а не в CI: без движков он честно скажет об этом.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

PAGES = {
    "home": "/webapp/home",
    "week": "/webapp/week",
    "week_summary": "/webapp/week/summary",
    "money": "/webapp/money",
    "more": "/webapp/more",
    "tx": "/webapp/tx",
    "dues": "/webapp/dues",
    "profile": "/webapp",
    "slots": "/webapp/slots",
    "notifications": "/webapp/notifications",
    "broadcast": "/webapp/broadcast",
    "feedback": "/webapp/feedback",
    "reconcile": "/webapp/reconcile",
    "services": "/webapp/services",
    "admin_people": "/webapp/admin/t/people",
    "admin_pending": "/webapp/admin/t/pending",
    "admin_audit": "/webapp/admin/t/audit",
    "admin_agent": "/webapp/admin/t/agent",
    "admin_feedback": "/webapp/admin/t/feedback",
    "ui": "/webapp/ui",
}

WIDTHS = (390, 320)

INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
        "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

MEASURE = r"""
() => {
  const all = [...document.querySelectorAll('body *')];
  const visible = el => el.offsetParent !== null || el.getClientRects().length > 0;
  const targets = [...document.querySelectorAll('a,button,input,select,textarea')]
    .filter(visible).map(el => {
      const r = el.getBoundingClientRect();
      return {tag: el.tagName.toLowerCase(),
              text: (el.innerText || el.id || '').trim().slice(0, 28),
              w: Math.round(r.width), h: Math.round(r.height)};
    }).filter(t => t.h > 0);
  const small = targets.filter(t => t.h < 44 || t.w < 44);

  let minFont = 99, minFontWhere = '';
  all.forEach(el => {
    if (!(el.innerText || '').trim() || el.children.length) return;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size < minFont) { minFont = size; minFontWhere = (el.innerText || '').trim().slice(0, 24); }
  });

  const text = document.body.innerText || '';
  const emoji = [...new Set(text.match(
    /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu) || [])];

  return {
    title: document.title,
    height: Math.round(document.body.scrollHeight),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    hscroll: all.filter(el => {
      const s = getComputedStyle(el);
      return (s.overflowX === 'auto' || s.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
    }).map(el => (el.id || el.className || el.tagName).toString().slice(0, 40)),
    targets: targets.length, small: small.length,
    smallSamples: small.slice(0, 3).map(t => t.tag + ' «' + t.text + '» ' + t.w + 'x' + t.h),
    minFont: minFont === 99 ? null : minFont, minFontWhere,
    emoji, emojiCount: emoji.length,
  };
}
"""


def measure() -> dict:
    try:
        import uvicorn
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover
        raise SystemExit(f"нужен playwright: {error}") from error

    from tests.webapp.server import app as stand

    port = 8795
    server = uvicorn.Server(uvicorn.Config(stand, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:  # pragma: no cover
        raise SystemExit("стенд Mini App не поднялся")

    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": 844})
            page = context.new_page()
            for name, path in PAGES.items():
                page.goto(f"http://127.0.0.1:{port}{path}{HASH}", wait_until="load")
                page.wait_for_timeout(600)
                result[f"{name}@{width}"] = page.evaluate(MEASURE)
            context.close()
        browser.close()
    return result


def markdown(result: dict) -> str:
    lines = ["| Экран | Высота | Переполнение | Гориз. скролл | Цели < 44 px | "
             "Мелкий шрифт | Эмодзи |", "|---|---|---|---|---|---|---|"]
    for width in WIDTHS:
        for name in PAGES:
            row = result.get(f"{name}@{width}")
            if not row:
                continue
            lines.append(
                f"| `{PAGES[name]}` @{width} | {row['height']} px | {row['overflow']} px | "
                f"{len(row['hscroll']) or '—'} | {row['small']} из {row['targets']} | "
                f"{row['minFont'] or '—'} px | {' '.join(row['emoji']) or '—'} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    result = measure()
    if "--json" in argv:
        path = pathlib.Path(argv[argv.index("--json") + 1])
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"записано: {path}")
    if "--markdown" in argv:
        print(markdown(result))
    elif "--json" not in argv:
        for key, row in result.items():
            print(f"{key:24} h={row['height']:5} overflow={row['overflow']} "
                  f"small={row['small']}/{row['targets']} font<={row['minFont']} "
                  f"hscroll={row['hscroll']} emoji={row['emoji']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Замер V-048 (пересмотр): что даёт снятие кнопки «Отметить сегодня» на неделе.

Что делает: поднимает стенд `tests.webapp.server` на свободном порту, открывает
`/webapp/week` как Telegram (390×844 и 320×693, часы остановлены на 01.11.2026 —
сегодня в фикстуре воскресенье, не отмечено), читает геометрию. Если кнопка
`#mark-today` ещё в разметке (дерево до правки), скрипт убирает её из живого DOM
(`remove()`) и повторяет замер — так воспроизводятся числа «до/после» §2.6.
В сведённом дереве кнопки уже нет: скрипт фиксирует это и меряет состояние после
правки. Заодно проверяет поведение: нажатие на строку сегодняшнего дня открывает
лист дня и не отправляет POST, а после выбора варианта и закрытия листа уходит
ровно один POST.

Зачем: числа `docs/development/149` §2.6–2.7 должны воспроизводиться, а не жить
в отчёте. Правка разметки — в карте реализации, здесь только измеритель.

Запуск из корня репозитория:

    env -u PYTHONPATH .venv/bin/python scripts/probes/week_no_mark_today.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import socket
import sys
import threading
import time
from typing import Any
from urllib.parse import quote

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

VIEWPORTS = {"390x844": (390, 844), "320x693": (320, 693)}
INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
        "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")
FIXED = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
TODAY_SLOT = 6
#: Адрес экрана держим константой: инвентарь действий (`scripts/audit_actions.py`)
#: считает владельцем ссылки функцию, в которой стоит строка, — на уровне модуля
#: владельцем становится имя скрипта, и в отчёте 117 нет выдуманных «скриптов».
WEEK_PATH = "/webapp/week"

GEOM = """() => {
  var out = {doc: document.documentElement.scrollHeight,
             client: document.documentElement.clientHeight};
  ['#day-list', '#mark-today', '#fill-template', '#load-from-sheet', '#to-summary',
   '#save-template', '#clear-week'].forEach(function (sel) {
    var n = document.querySelector(sel);
    if (!n) { out[sel] = null; return; }
    var b = n.getBoundingClientRect();
    out[sel] = {y: Math.round(b.y), bottom: Math.round(b.y + b.height),
                h: Math.round(b.height)};
  });
  var tab = document.querySelector('.ui-tabbar').getBoundingClientRect();
  out.tabbar = {y: Math.round(tab.y), h: Math.round(tab.height)};
  var t = document.querySelector('[data-day-slot="6"]');
  var tb = t.getBoundingClientRect();
  out.today = {h: Math.round(tb.height),
               meta: t.querySelector('.ui-row__meta').textContent,
               meta_h: Math.round(t.querySelector('.ui-row__meta').getBoundingClientRect().height),
               state: t.querySelector('.ui-day__state').textContent,
               flag_w: Math.round(t.querySelector('.ui-day__today').getBoundingClientRect().width)};
  return out;
}"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_stand(port: int) -> Any:
    import uvicorn

    from tests.webapp.server import app as stand

    server = uvicorn.Server(uvicorn.Config(stand, host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.05)
    raise RuntimeError("стенд Mini App не поднялся")


def load(page) -> None:
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=15000)
    page.wait_for_timeout(400)


def probe_week(browser, base: str, report: dict) -> None:
    for tag, (width, height) in VIEWPORTS.items():
        context = browser.new_context(viewport={"width": width, "height": height},
                                      color_scheme="light")
        page = context.new_page()
        errors: list[str] = []
        posts: list[str] = []
        page.on("pageerror", lambda exc, bucket=errors: bucket.append(str(exc)))
        page.on("request",
                lambda r, bucket=posts: bucket.append(r.url) if r.method == "POST" else None)
        page.goto(f"{base}{WEEK_PATH}{HASH}", wait_until="load")
        page.clock.install(time=FIXED)
        page.reload(wait_until="load")
        load(page)

        item: dict = {"viewport": f"{width}x{height}"}
        item["before"] = page.evaluate(GEOM)
        if item["before"]["#mark-today"] is None:
            # Сведённое дерево: кнопки в разметке уже нет, убирать нечего.
            item["button_in_markup"] = False
            item["saved_px"] = None
            item["after"] = item["before"]
        else:
            item["button_in_markup"] = True
            page.evaluate("document.getElementById('mark-today').remove()")
            page.wait_for_timeout(150)
            item["after"] = page.evaluate(GEOM)
            item["saved_px"] = item["before"]["doc"] - item["after"]["doc"]
        tab = item["after"]["tabbar"]["y"]
        item["under_tabbar_px"] = {
            key: item["after"][key]["bottom"] - tab
            for key in ("#fill-template", "#load-from-sheet", "#to-summary",
                        "#save-template", "#clear-week")
            if item["after"][key] and item["after"][key]["bottom"] > tab
        }
        # Подсказка в пустой второй строке сегодняшнего дня: есть ли она и чего стоит.
        item["hint_text"] = page.evaluate(
            "() => document.querySelector('[data-day-slot=\"6\"] .ui-row__meta').textContent")
        item["hint"] = page.evaluate(GEOM)
        item["hint_without_px"] = page.evaluate(
            """() => {
                 var m = document.querySelector('[data-day-slot="6"] .ui-row__meta');
                 var keep = m.textContent;
                 m.textContent = '';
                 var bare = document.documentElement.scrollHeight;
                 m.textContent = keep;
                 return bare;
               }""")
        item["hint_cost_px"] = item["hint"]["doc"] - item["hint_without_px"]

        # Поведение: клик по строке сегодня.
        page.reload(wait_until="load")
        load(page)
        posts.clear()
        page.locator(f'[data-day-slot="{TODAY_SLOT}"]').click()
        page.wait_for_timeout(500)
        item["click"] = {"sheet": page.locator(".ui-sheet").count(),
                         "posts_до_выбора": len(posts),
                         "выбран": page.evaluate(
                             """() => Array.prototype.map.call(
                                 document.querySelectorAll('#day-modes .ui-radio-row--on'),
                                 function (n) { return n.getAttribute('data-mode'); })""")}
        page.locator('#day-modes [data-mode="busy"]').click()
        page.wait_for_timeout(150)
        page.locator(".ui-sheet__actions .ui-btn--primary").first.click()
        page.wait_for_timeout(1500)
        item["click"]["posts_после_закрытия"] = [url[len(base):] for url in posts]
        item["click"]["sheet_после_закрытия"] = page.locator(".ui-sheet").count()
        item["pageerrors"] = errors
        report[tag] = item
        context.close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    port = free_port()
    start_stand(port)
    base = f"http://127.0.0.1:{port}"
    report: dict = {}
    # Стенд живёт в демон-потоке: процесс завершается, поток уходит вместе с ним.
    # `should_exit` не трогаем — vulture читает это как мёртвый атрибут.
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        probe_week(browser, base, report)
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

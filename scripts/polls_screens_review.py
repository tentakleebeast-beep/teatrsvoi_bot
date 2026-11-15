#!/usr/bin/env python3
"""Состояния экранов опросов на телефоне: черновик, 11 вопросов, адресаты, сводка, отказ.

Зачем отдельно от `scripts/ui_review.py`: тот меряет экран «как открылся», а у
конструктора и сводки дефект живёт в **состоянии** — пустой черновик, черновик с 11
вопросами (сколько строк видно в первом кадре), свёрнутые адресаты на 20 человек,
свёрнутая сводка на 11 вопросов, отказ по праву словами. Плюс ширина 320, которую
`ui_review` снимает, но не «нажимает».

Мерки — те же, что у `tests/webapp/test_miniapp_polls.py` (56 px строка, ≥ 8 вопросов
в кадре, сводка ≤ 1 000 px), плюс переполнение вбок и снимок каждого состояния.

    env -u PYTHONPATH .venv/bin/python -m uvicorn tests.webapp.server:app --port 8765 &
    env -u PYTHONPATH .venv/bin/python scripts/polls_screens_review.py \\
        --base-url http://127.0.0.1:8765 --out /tmp/polls-states.json \\
        --markdown /tmp/polls-states.md --shots tests/webapp/screenshots/polls-states
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: Роль задаётся заголовком стенда `tests/webapp/server.py`; токен один на всех.
ROLE_HEADER = "X-SVOI-Role"
TOKEN = "test-token"

#: Так Telegram передаёт данные Mini App: без хеша `telegram-web-app.js` не поднимет
#: `window.Telegram`, страница не получит токен и конструктор останется пустым.
#: Тот же приём, что в `tests/webapp/conftest.py`.
INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
TELEGRAM_HASH = (f"#tgWebAppData={INIT_DATA}"
                 "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")
HEIGHT = "() => document.documentElement.scrollHeight"
VISIBLE_QUESTIONS = """
() => {
  const frame = window.innerHeight;
  return Array.from(document.querySelectorAll('#questions .ui-row'))
    .filter((row) => row.getBoundingClientRect().bottom <= frame + 1).length;
}
"""
ROW_HEIGHTS = ("() => Array.from(document.querySelectorAll('#questions .ui-row'))"
               ".map((row) => Math.round(row.getBoundingClientRect().height))")

#: Состояние → (адрес, роль, что сделать после загрузки, чего ждать).
STATES = [
    ("пустой-черновик", "/webapp/polls/edit", "admin", None, "#questions .ui-row"),
    ("11-вопросов", "/webapp/polls/edit?poll_id=7", "admin", None, "#questions .ui-row"),
    ("адресаты", "/webapp/polls/edit?poll_id=7&audience=1", "admin", "audience",
     "#people .ui-row"),
    ("сводка", "/webapp/polls/results?poll_id=7", "admin", None, "#questions details"),
    ("отказ-org", "/webapp/polls", "org", None, "#denied:not([hidden])"),
    ("отказ-troupe", "/webapp/polls", "troupe", None, "#denied:not([hidden])"),
    ("отказ-troupe-конструктор", "/webapp/polls/edit?poll_id=7", "troupe", None,
     "#denied:not([hidden])"),
]


def measure(browser, base_url: str, width: int, scheme: str, state: tuple) -> dict:
    name, path, role, action, selector = state
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  color_scheme=scheme, device_scale_factor=2)
    context.set_extra_http_headers({ROLE_HEADER: role})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    url = f"{base_url}{path}{TELEGRAM_HASH}"
    try:
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_selector(selector, timeout=10000)
        page.wait_for_timeout(400)
        record = {
            "state": name, "path": path, "role": role, "width": width, "scheme": scheme,
            "overflow": page.evaluate(OVERFLOW),
            "height": page.evaluate(HEIGHT),
            "visible_questions": page.evaluate(VISIBLE_QUESTIONS),
            "row_heights": page.evaluate(ROW_HEIGHTS),
            "questions": page.locator("#questions .ui-row").count(),
            "people": page.locator("#people .ui-row").count(),
            "summary_blocks": page.locator("#questions details").count(),
            "summary_open": page.evaluate(
                "() => Array.from(document.querySelectorAll('#questions details'))"
                ".filter((node) => node.open).length"),
            "denied_visible": page.locator("#denied").is_visible()
            if page.locator("#denied").count() else False,
            "body_text": " ".join(page.inner_text("body").split())[:400],
            "errors": errors,
            "shot": None,
        }
    except Exception as exc:  # noqa: BLE001 — «не открылось» это факт замера
        record = {"state": name, "path": path, "role": role, "width": width,
                  "scheme": scheme, "overflow": None, "height": None,
                  "visible_questions": None, "row_heights": [], "questions": 0,
                  "people": 0, "summary_blocks": 0, "summary_open": 0,
                  "denied_visible": False, "body_text": "", "errors": errors + [str(exc)],
                  "shot": None}
    context.close()
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Состояния экранов опросов на телефоне.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--engine", default="chromium", choices=("chromium", "webkit"))
    parser.add_argument("--widths", default="390,320")
    parser.add_argument("--schemes", default="light,dark")
    parser.add_argument("--shots", help="каталог для снимков")
    parser.add_argument("--out", help="JSON с мерками")
    parser.add_argument("--markdown", help="Markdown-таблица")
    args = parser.parse_args(argv)
    if not (args.out or args.markdown or args.shots):
        parser.error("нужен хотя бы один из ключей --out, --markdown, --shots")

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    records = []
    playwright = sync_playwright().start()
    browser = getattr(playwright, args.engine).launch()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    try:
        for width in [int(w) for w in args.widths.split(",")]:
            for scheme in [s.strip() for s in args.schemes.split(",")]:
                for state in STATES:
                    record = measure(browser, args.base_url, width, scheme, state)
                    records.append(record)
                    if shots:
                        target = shots / f"{state[0]}-{width}-{scheme}.png"
                        _shoot(browser, args.base_url, width, scheme, state, target)
                        record["shot"] = str(target)
    finally:
        browser.close()
        playwright.stop()

    if args.out:
        Path(args.out).write_text(json.dumps({"records": records}, ensure_ascii=False,
                                             indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {args.out}")
    if args.markdown:
        Path(args.markdown).write_text(_markdown(records), encoding="utf-8")
        print(f"Markdown: {args.markdown}")
    print(f"состояний {len(records)}")
    return 0


def _shoot(browser, base_url, width, scheme, state, target: Path) -> None:
    name, path, role, _action, selector = state
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  color_scheme=scheme, device_scale_factor=2)
    context.set_extra_http_headers({ROLE_HEADER: role})
    page = context.new_page()
    try:
        page.goto(f"{base_url}{path}{TELEGRAM_HASH}", wait_until="load", timeout=30000)
        page.wait_for_selector(selector, timeout=10000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(target), full_page=True)
    finally:
        context.close()


def _markdown(records: list[dict]) -> str:
    lines = ["| Состояние | Ширина | Схема | Вопросов | Видно в кадре | Строка, px | Адресатов "
             "| Блоков сводки | Сводка, px | Отказ словами | Вбок | Ошибок JS |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for record in records:
        heights = record["row_heights"]
        lines.append(
            f"| {record['state']} | {record['width']} | {record['scheme']} "
            f"| {record['questions']} | {record['visible_questions']} "
            f"| {max(heights) if heights else '—'} | {record['people']} "
            f"| {record['summary_blocks']} | {record['height']} "
            f"| {'да' if record['denied_visible'] else 'нет'} | {record['overflow']} "
            f"| {len(record['errors'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

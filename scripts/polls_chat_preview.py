#!/usr/bin/env python3
"""Опрос в чате участника: текст и клавиатура из `poll_ui` в вёрстке мокапа 162 `03`.

Зачем: чат рисует Telegram, но текст и разметку кнопок собираем мы — значит проверять
надо ровно их. Скрипт берёт настоящие `poll_ui.question_text` / `keyboard_rows` /
`closed_text` / `done_text` и `api/polls.py:_invitation_markup` (первое сообщение —
push, кнопки едут в `payload.markup`), кладёт их в вёрстку `03-chat-question.html`
и меряет Playwright'ом на 320 и 390 в двух схемах.

Что печатается на каждое сообщение (AC-3 карты E8):

    длина текста (предел Telegram 4096)
    самая широкая подпись кнопки и сколько осталось до края строки
    кнопок в самом широком ряду (Telegram держит 3 без давки)
    самый длинный callback_data в байтах (предел 18 у спецификации, 64 у Telegram)
    эмодзи, сырые `<`/`>`/`&`, двойные пробелы, повтор слова «вопрос»

Telegram может отличаться от мокапа (шрифт, отступы, переносы) — поэтому это замер
нашей разметки, а не снимок клиента.

    env -u PYTHONPATH .venv/bin/python scripts/polls_chat_preview.py \\
        --out /tmp/polls-chat.json --markdown /tmp/polls-chat.md \\
        --shots tests/webapp/screenshots/polls-chat
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services" / "tg-gateway" / "app"
TOOL_API = ROOT / "services" / "tool-api"
for path in (str(ROOT), str(TOOL_API), str(GATEWAY)):
    if path not in sys.path:
        sys.path.insert(0, path)

import poll_ui  # noqa: E402

TEXT_LIMIT = 4096
CB_LIMIT = poll_ui.CB_LIMIT
BUTTONS_PER_ROW = 3
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

RUN_ID = 3_214_567          # семизначный: худший случай для callback_data
POLL_TITLE = "Анкета организаторам"
TOTAL = 11

#: Пять типов вопроса (162 §3 D2). Тексты и подписи — из анкеты 161 §9, чтобы
#: мерить то, что человек увидит на самом деле, а не «кнопка».
STEPS = {
    "single": {
        "kind": "single", "position": 2, "text": "Как вы выбираете окно для репетиции?",
        "required": True, "free_text": False, "max_choices": None,
        "options": [{"code": "a1", "label": "Расписание студийцы"},
                    {"code": "a2", "label": "Календарь"},
                    {"code": "a3", "label": "Звонки"},
                    {"code": "a4", "label": "Сообщения"}],
    },
    "multi": {
        "kind": "multi", "position": 7, "text": "Какие события вы заводите в календарь?",
        "required": True, "free_text": False, "max_choices": 4,
        "options": [{"code": "a1", "label": "Репетиции"},
                    {"code": "a2", "label": "Спектакли"},
                    {"code": "a3", "label": "Звонки"},
                    {"code": "a4", "label": "Встречи с родителями"}],
    },
    "yesno": {
        "kind": "yesno", "position": 4, "text": "Нужен ли отдельный чат для зрителей?",
        "required": True, "free_text": False, "max_choices": None, "options": [],
    },
    "number": {
        "kind": "number", "position": 5,
        "text": "Сколько часов в неделю уходит на согласование окон?",
        "required": True, "free_text": False, "max_choices": None, "options": [],
    },
    "text": {
        "kind": "text", "position": 9, "text": "Что улучшить в расписании?",
        "required": False, "free_text": False, "max_choices": None, "options": [],
    },
}

CSS = """
:root { color-scheme: light; --chat:#7d9cb3; --bubble:#fff; --text:#1b2733;
        --muted:#707579; --key:#eef3f9; --key-border:rgba(0,0,0,.08); --key-text:#1b2733;
        --quiet:#f4f4f5; --quiet-text:#4d5257; --hint:#1459e3; --caption:#20313f; }
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark; --chat:#0e1621; --bubble:#182533; --text:#f5f5f5;
          --muted:#7d8b99; --key:#202b36; --key-border:rgba(255,255,255,.08);
          --key-text:#fff; --quiet:#1f2b36; --quiet-text:#c8d3de; --hint:#6ab3f3;
          --caption:#c8d3de; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--chat); color:var(--text); font:16px/1.35 -apple-system,
       "SF Pro Text", Roboto, sans-serif; }
.wrap { padding:12px; display:flex; flex-direction:column; gap:12px; }
.caption { font-size:13px; color:var(--caption); }
.bubble { background:var(--bubble); border-radius:12px; padding:10px 12px; max-width:330px;
          box-shadow:0 1px 1px rgba(0,0,0,.12); align-self:flex-start; }
.progress { font-size:13px; color:var(--muted); margin-bottom:6px; }
.topic { font-weight:600; margin-bottom:8px; white-space:pre-wrap; }
.note { font-size:13px; color:var(--muted); margin-top:6px; }
.keys { display:flex; flex-direction:column; gap:6px; margin-top:10px; }
.keys .row { display:flex; gap:6px; }
.key { flex:1; min-height:44px; display:flex; align-items:center; justify-content:center;
       background:var(--key); border:1px solid var(--key-border); border-radius:8px;
       font-size:15px; color:var(--key-text); padding:6px 8px; text-align:center;
       overflow:hidden; }
.key--quiet { background:var(--quiet); color:var(--quiet-text); flex:0 1 auto; padding:0 14px; }
.key--hint { background:var(--bubble); color:var(--hint); border:1px dashed var(--hint); }
"""

#: Мерки подписи: натуральная ширина строки против внутренней ширины кнопки.
MEASURE = """
() => {
  const keys = [...document.querySelectorAll('.key')].map((node) => {
    const style = getComputedStyle(node);
    const span = document.createElement('span');
    span.textContent = node.textContent;
    span.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font:' +
      style.font;
    document.body.appendChild(span);
    const natural = span.getBoundingClientRect().width;
    span.remove();
    const pad = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const inner = node.getBoundingClientRect().width - pad;
    const row = node.closest('.row');
    return {label: node.textContent.trim(), natural: natural, inner: inner,
            rowWidth: row ? row.getBoundingClientRect().width : inner,
            keysInRow: row ? row.children.length : 1};
  });
  return {
    keys: keys,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    bubbleWidth: (document.querySelector('.bubble') || {getBoundingClientRect:
      () => ({width: 0})}).getBoundingClientRect().width,
  };
}
"""


def step_for(kind: str) -> dict:
    step = dict(STEPS[kind])
    step.update({"run_id": RUN_ID, "total": TOTAL, "title": POLL_TITLE,
                 "chosen": [], "chosen_text": None, "closed": False, "done": False})
    return step


def invitation(step: dict) -> tuple[str, list[list[tuple[str, str]]]]:
    """Первое сообщение push-ом: `title` + `body` собирает `message_text`, кнопки — API."""
    from app.api import polls as api_polls  # noqa: PLC0415 — тяжёлый импорт, нужен точный

    markup = api_polls._invitation_markup(RUN_ID, step)["inline_keyboard"]
    rows = [[(button["text"], button["callback_data"]) for button in line] for line in markup]
    text = f"{POLL_TITLE} · вопрос {step['position']} из {step['total']}\n\n{step['text']}"
    return text, rows


def messages(kind: str) -> list[dict]:
    step = step_for(kind)
    invite_text, invite_rows = invitation(step)
    closed = dict(step, closes_at="2026-11-20T21:00:00+03:00", closed=True)
    done = dict(step, done=True, position=TOTAL)
    return [
        {"state": "приглашение", "text": invite_text, "rows": invite_rows,
         "note": "сообщение из очереди notification: кнопки внутри payload.markup"},
        {"state": "повтор", "text": poll_ui.question_text(step),
         "rows": poll_ui.keyboard_rows(step),
         "note": "следующий шаг и «Продолжить» из меню: текст правит то же сообщение"},
        {"state": "закрыт", "text": poll_ui.closed_text(closed), "rows": [],
         "note": "опрос закрыт: сообщение без кнопок (§14.4 п. 7)"},
        {"state": "итог", "text": poll_ui.done_text(done), "rows": [],
         "note": "последний вопрос пройден: ответов N из M"},
    ]


def bubble_html(message: dict, caption: str) -> str:
    keys = ""
    if message["rows"]:
        rows = []
        for row in message["rows"]:
            cells = "".join(
                f'<div class="key{" key--hint" if data.startswith("p:f:") else ""}'
                f'{" key--quiet" if label in ("Назад", "Пропустить", "Дальше") else ""}"'
                f'>{label}</div>'
                for label, data in row)
            rows.append(f'<div class="row">{cells}</div>')
        keys = '<div class="keys">' + "".join(rows) + "</div>"
    lines = message["text"].split("\n")
    topic = lines[2:] if len(lines) > 2 else [""]
    head, body = lines[0], "\n".join(topic).strip("\n")
    return (f'<div class="caption">{caption}</div>'
            f'<div class="bubble"><div class="progress">{head}</div>'
            f'<div class="topic">{body}</div>{keys}'
            f'<div class="note">{message["note"]}</div></div>')


def page_html(message: dict, caption: str) -> str:
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<style>{CSS}</style></head><body><div class=\"wrap\">"
            f"{bubble_html(message, caption)}</div></body></html>")


def check_text(text: str, rows: list[list[tuple[str, str]]]) -> dict:
    labels = [label for row in rows for label, _ in row]
    codes = [data for row in rows for _, data in row]
    return {
        "text_chars": len(text),
        "text_limit_ok": len(text) <= TEXT_LIMIT,
        "rows": len(rows),
        "buttons": len(labels),
        "max_buttons_in_row": max((len(row) for row in rows), default=0),
        "max_callback_bytes": max((len(code.encode()) for code in codes), default=0),
        "callback_ok": all(len(code.encode()) <= CB_LIMIT for code in codes),
        "emoji": EMOJI.findall(text) + [e for e in EMOJI.findall(" ".join(labels))],
        "raw_angle_or_amp": sorted({ch for ch in text if ch in "<>&"}),
        "double_space": "  " in text or any("  " in label for label in labels),
        "vopros_count": len(re.findall(r"вопрос", text, flags=re.IGNORECASE)),
        "empty_lines": "\n\n\n" in text,
    }


def render(engine: str, widths: list[int], schemes: list[str], shots: Path | None,
           out: Path | None, markdown: Path | None) -> int:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    records: list[dict] = []
    argv = []
    playwright = sync_playwright().start()
    browser = getattr(playwright, engine).launch()
    try:
        for kind in STEPS:
            for message in messages(kind):
                checks = check_text(message["text"], message["rows"])
                records.append({"kind": kind, "state": message["state"], "text": message["text"],
                                "rows": message["rows"], "note": message["note"],
                                "checks": checks, "measure": {}})
        for record in records:
            for width in widths:
                for scheme in schemes:
                    context = browser.new_context(viewport={"width": width, "height": 844},
                                                  color_scheme=scheme,
                                                  device_scale_factor=2 if shots else 1)
                    page = context.new_page()
                    caption = f"{record['kind']} · {record['state']} · {width} · {scheme}"
                    page.set_content(page_html(record, caption))
                    values = page.evaluate(MEASURE)
                    keys = values["keys"]
                    worst = min(keys, key=lambda k: k["inner"] - k["natural"], default=None)
                    record["measure"][f"{width}-{scheme}"] = {
                        "overflow": values["overflow"],
                        "bubble": round(values["bubbleWidth"], 1),
                        "max_buttons_in_row": max((k["keysInRow"] for k in keys), default=0),
                        "widest_label": max((round(k["natural"], 1) for k in keys), default=0),
                        "narrowest_inner": min((round(k["inner"], 1) for k in keys),
                                               default=None),
                        "min_slack": None if worst is None else round(
                            worst["inner"] - worst["natural"], 1),
                        "worst_label": worst["label"] if worst else None,
                        "clipped": [k["label"] for k in keys
                                    if k["natural"] - k["inner"] > 1],
                    }
                    if shots and record["state"] == "приглашение":
                        shots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(shots / f"{record['kind']}-{width}-{scheme}.png"),
                                        full_page=True)
                    context.close()
                    argv.append(1)
    finally:
        browser.close()
        playwright.stop()

    if out:
        Path(out).write_text(json.dumps({"engine": engine, "records": records},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {out}")
    if markdown:
        Path(markdown).write_text(markdown_table(records, widths, schemes), encoding="utf-8")
        print(f"Markdown: {markdown}")
    print(f"сообщений {len(records)}, замеров {len(argv)}")
    return 0


def markdown_table(records: list[dict], widths: list[int], schemes: list[str]) -> str:
    lines = ["| Тип | Состояние | Длина | Подпись, px | Порог, px | Остаток, px | Кнопок "
             "в ряду | callback, байт | Эмодзи | <>& | Двойные пробелы | «вопрос» |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for record in records:
        for width in widths:
            for scheme in schemes:
                m = record["measure"][f"{width}-{scheme}"]
                c = record["checks"]
                threshold = "—" if m["narrowest_inner"] is None else m["narrowest_inner"]
                slack = "—" if m["min_slack"] is None else m["min_slack"]
                lines.append(
                    f"| {record['kind']} | {record['state']} {width}/{scheme} "
                    f"| {c['text_chars']} | {m['widest_label']} | {threshold} "
                    f"| {slack} | {m['max_buttons_in_row']} | {c['max_callback_bytes']} "
                    f"| {len(c['emoji'])} | {''.join(c['raw_angle_or_amp']) or '—'} "
                    f"| {'да' if c['double_space'] else 'нет'} | {c['vopros_count']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Текст и клавиатура опроса в вёрстке мокапа 162 `03`.")
    parser.add_argument("--engine", default="chromium", choices=("chromium", "webkit"))
    parser.add_argument("--widths", default="320,390")
    parser.add_argument("--schemes", default="light,dark")
    parser.add_argument("--shots", help="каталог для снимков приглашения")
    parser.add_argument("--out", help="JSON с мерками")
    parser.add_argument("--markdown", help="Markdown-таблица")
    parser.add_argument("--print-texts", action="store_true",
                        help="напечатать тексты и подписи в консоль")
    args = parser.parse_args(argv)
    if args.print_texts:
        for kind in STEPS:
            for message in messages(kind):
                print(f"=== {kind} · {message['state']} ===")
                print(message["text"])
                for row in message["rows"]:
                    print("  [", "] [".join(f"{label} → {data}" for label, data in row), "]")
        return 0
    return render(args.engine, [int(w) for w in args.widths.split(",")],
                  [s.strip() for s in args.schemes.split(",")],
                  Path(args.shots) if args.shots else None,
                  Path(args.out) if args.out else None,
                  Path(args.markdown) if args.markdown else None)


if __name__ == "__main__":
    raise SystemExit(main())

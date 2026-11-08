#!/usr/bin/env python3
"""Ежедневный отчёт «требует вас»: блокировки, мёртвые карты, очередь.

Зачем: блокировки и карты без исполнителя/класса всплывают только когда владелец
сам спросит. 01.11.2026 на доске висели три блокировки — две оказались чужими
экспериментами, третья ждала ручного шага на сервере, и ни одна не была поднята
сама. Отчёт показывает ровно то, что требует человека, и молчит только тогда,
когда сбор сломался (тогда приходит причина, а не тишина).

Что печатает (одна экранная страница, без эмодзи, только текст):
  требует вас — блокировки старше 6 часов и любые «нужен ответ»: карта, вопрос,
                 сколько ждёт. Блокировка без причины и без комментария попадает
                 сюда сразу, с пометкой «нет причины»: это сигнал, что карту
                 завели неправильно;
  мёртвые      — карты без исполнителя и карты без класса приоритета, по строке
                 на вид, с числом и именами;
  очередь      — три первые карты в порядке диспетчера (priority DESC,
                 created_at ASC);
  итог         — сделано за сутки, в работе, в ревью.

  env -u PYTHONPATH .venv/bin/python scripts/cron/kanban_daily.py --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/cron/kanban_daily.py

Код выхода: 0 — отчёт собран и отправлен; 1 — сбор не удался или Telegram отказал
(причина уходит в чат текстом); 2 — нет токена/адресата.

Отчёт только читает доску: база открывается `mode=ro`, статусы карт не меняются.

Ловушка HERMES_HOME: у профиля воркера канбана свой HERMES_HOME, и доска тогда
ищется в `<профиль>/kanban/boards` — там её нет. Поэтому переменная снимается
перед поиском базы (или задаётся явно `--hermes-home`), как в недельной сводке.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "scripts"))

import notify_admin  # noqa: E402

BOARD = "teatrsvoi"
HOUR = 3600
DAY = 24 * HOUR
SAMARA = timezone(timedelta(hours=4))  # UTC+4, без перехода на летнее время

# Что считается живой картой: done и архив — история, её не проверяем.
LIVE_STATUSES = ("ready", "todo", "blocked", "scheduled", "running", "review", "triage")
# Диспетчер берёт ready; todo ждёт родителей — обе стоят в очереди.
QUEUE_STATUSES = ("ready", "todo")
# Приоритет — класс из набора {1..5} = P4..P0. 0 и пусто — класса нет.
VALID_PRIORITIES = frozenset({1, 2, 3, 4, 5})
CLASS_BY_PRIORITY = {5: "P0", 4: "P1", 3: "P2", 2: "P3", 1: "P4"}
# Вид блокировки по-русски: в чате владельцу читать «нужен ответ», а не needs_input.
KIND_TITLES = {
    "needs_input": "нужен ответ",
    "capability": "нужна ручная работа",
    "dependency": "ждёт другую карту",
    "transient": "временный сбой",
}

BLOCK_WAIT_H = 6        # блокировка старше этого — уже требует человека
BLOCK_LIMIT = 5         # сколько блокировок перечислять целиком
QUEUE_LIMIT = 3         # «три первые карты» из карты задания
NAME_LIMIT = 46         # длина названия карты в строке
QUESTION_LIMIT = 140    # длина вопроса блокировки
LIMIT = 1200            # одна экранная страница; Telegram режет на 4096


class CollectionError(Exception):
    """Собрать отчёт не удалось — сообщаем причину текстом, а не молчим."""


def find_db(board: str = BOARD, hermes_home: str | None = None) -> Path:
    """Путь к базе доски: `<home>/kanban/boards/<board>/kanban.db`.

    HERMES_HOME снимается: иначе в профиле воркера доска ищется не там и
    отчёт молча выходит пустым.
    """
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    db = home / "kanban" / "boards" / board / "kanban.db"
    if not db.is_file():
        legacy = home / "kanban.db"
        if board == "default" and legacy.is_file():
            return legacy
        raise CollectionError(f"доска {board} не найдена: нет файла {db}")
    return db


def _connect(db: Path) -> sqlite3.Connection:
    """Только чтение: отчёт не имеет права менять доску."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CollectionError(f"база доски не открылась: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _payload(raw: str | None) -> tuple[str, str]:
    """Вид и причина из payload события `blocked`; мусор не ломает отчёт."""
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("kind") or ""), str(data.get("reason") or "").strip()


def _last_comment(conn: sqlite3.Connection, task_id: str, since: int) -> str:
    """Последний комментарий после блокировки: он и есть причина, если в событии пусто."""
    row = conn.execute(
        "select body from task_comments where task_id = ? and created_at >= ? "
        "order by created_at desc, id desc limit 1", (task_id, since)).fetchone()
    return (row["body"] or "").strip() if row else ""


def _has_class(priority: object) -> bool:
    return isinstance(priority, int) and priority in VALID_PRIORITIES


def _class_name(priority: object) -> str:
    """Класс приоритета (P0..P4); 0 и пусто — «без класса»."""
    if isinstance(priority, int) and priority in VALID_PRIORITIES:
        return CLASS_BY_PRIORITY[priority]
    return "без класса"


def _blocks(conn: sqlite3.Connection, cards: list[dict], now: int) -> list[dict]:
    """Блокировки, которые уже требуют человека: любые «нужен ответ» и всё старше 6 ч."""
    out: list[dict] = []
    for card in cards:
        if card["status"] != "blocked":
            continue
        event = conn.execute(
            "select payload, created_at from task_events where task_id = ? and kind = 'blocked' "
            "order by created_at desc, id desc limit 1", (card["id"],)).fetchone()
        since = int(event["created_at"]) if event else int(card["created_at"])
        kind, reason = _payload(event["payload"] if event else None)
        kind = kind or str(card.get("block_kind") or "")
        reason = reason or _last_comment(conn, card["id"], since)
        age_h = max(0.0, (now - since) / HOUR)
        # Блокировка без причины показывается сразу и всегда: это не «ещё не срок»,
        # а дефект карты — человек должен узнать о нём на следующее утро.
        if kind != "needs_input" and age_h <= BLOCK_WAIT_H and reason:
            continue
        out.append({
            "id": card["id"],
            "title": card["title"],
            "kind": kind,
            "reason": reason,
            "age_h": age_h,
        })
    # Кто ждёт дольше — тот и первым: это и есть «требует вас».
    out.sort(key=lambda b: (-b["age_h"], b["id"]))
    return out


def _dead(cards: list[dict]) -> tuple[list[dict], list[dict]]:
    """Мёртвые карты: без исполнителя и без класса приоритета."""
    live = [c for c in cards if c["status"] in LIVE_STATUSES]
    no_assignee = [c for c in live if not str(c.get("assignee") or "").strip()]
    no_priority = [c for c in live if not _has_class(c.get("priority"))]
    return no_assignee, no_priority


def _queue(cards: list[dict], now: int) -> list[dict]:
    """Три первые карты в порядке диспетчера: priority DESC, created_at ASC."""
    queue = [c for c in cards if c["status"] in QUEUE_STATUSES]
    queue.sort(key=lambda c: (
        -(c["priority"] if _has_class(c["priority"]) else 0),
        c["created_at"],
        c["id"],
    ))
    return [{
        "id": c["id"],
        "title": c["title"],
        "class": _class_name(c["priority"]),
        "age_h": max(0.0, (now - int(c["created_at"])) / HOUR),
    } for c in queue[:QUEUE_LIMIT]]


def collect(db: Path, now: int | None = None) -> dict:
    """Собирает всё, что нужно отчёту, одним чтением доски."""
    moment = int(now if now is not None else time.time())
    conn = _connect(db)
    try:
        cards = [dict(r) for r in conn.execute(
            "select id, title, status, priority, assignee, created_at, completed_at, "
            "block_kind from tasks")]
        blocks = _blocks(conn, cards, moment)
        no_assignee, no_priority = _dead(cards)
        queue = _queue(cards, moment)
    except sqlite3.Error as exc:
        raise CollectionError(f"база доски не читается: {exc}") from exc
    finally:
        conn.close()

    done_day = [c for c in cards
                if c["status"] == "done" and c["completed_at"]
                and int(c["completed_at"]) >= moment - DAY]
    return {
        "blocks": blocks,
        "no_assignee": [{"id": c["id"], "title": c["title"]} for c in no_assignee],
        "no_priority": [{"id": c["id"], "title": c["title"]} for c in no_priority],
        "queue": queue,
        "done_day": len(done_day),
        "running": sum(1 for c in cards if c["status"] == "running"),
        "review": sum(1 for c in cards if c["status"] == "review"),
    }


def _short(text: object, limit: int) -> str:
    """Одна строка без переводов: длинное название не рвёт экран и режется по слову."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit - 1]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:") + "…"


def _wait(age_h: float) -> str:
    """Сколько ждёт: часами до двух суток, дальше сутками."""
    if age_h < 1:
        return "меньше часа"
    hours = int(age_h)
    if hours < 48:
        return f"{hours} ч"
    return f"{hours // 24} сут"


def _names(items: list[dict]) -> str:
    return ", ".join(f"{c['id']} «{_short(c['title'], NAME_LIMIT)}»" for c in items)


def format_report(data: dict, board: str = BOARD, now: datetime | None = None) -> str:
    """Текст отчёта: четыре блока, без эмодзи и вложений."""
    moment = (now or datetime.now(SAMARA)).astimezone(SAMARA)
    blocks = data.get("blocks") or []
    no_assignee = data.get("no_assignee") or []
    no_priority = data.get("no_priority") or []
    queue = data.get("queue") or []
    dead_total = len(no_assignee) + len(no_priority)

    lines = [
        "Ежедневный отчёт: требует вас",
        f"Доска {board}, {moment:%d.%m.%Y %H:%M}",
        "",
    ]

    if not blocks and not dead_total:
        lines.append("Требующих человека нет.")
    else:
        lines.append(f"Требует вас: {len(blocks)}")
        for block in blocks[:BLOCK_LIMIT]:
            kind = KIND_TITLES.get(block["kind"], "вид не указан")
            mark = "" if block["reason"] else ", нет причины"
            lines.append(f"- {block['id']} «{_short(block['title'], NAME_LIMIT)}» — "
                         f"{kind}, ждёт {_wait(block['age_h'])}{mark}")
            if block["reason"]:
                lines.append(f"  Вопрос: {_short(block['reason'], QUESTION_LIMIT)}")
        if len(blocks) > BLOCK_LIMIT:
            lines.append(f"  и ещё {len(blocks) - BLOCK_LIMIT} блокировок")
        lines.append("")

        lines.append(f"Мёртвые: {dead_total}")
        if no_assignee:
            lines.append(f"- без исполнителя: {len(no_assignee)} — {_names(no_assignee)}")
        if no_priority:
            lines.append(f"- без приоритета: {len(no_priority)} — {_names(no_priority)}")
        lines.append("")

    if queue:
        lines.append(f"Очередь: {len(queue)} первые")
        for number, card in enumerate(queue, start=1):
            lines.append(f"{number}. {card['id']} «{_short(card['title'], NAME_LIMIT)}» — "
                         f"{card['class']}, ждёт {_wait(card['age_h'])}")
    else:
        lines.append("Очередь: пусто")

    lines.append(f"Итог: сделано за сутки {data.get('done_day', 0)}, "
                 f"в работе {data.get('running', 0)}, в ревью {data.get('review', 0)}")
    return "\n".join(lines)[:LIMIT].rstrip()


def format_error(reason: str, board: str = BOARD,
                 now: datetime | None = None) -> str:
    """Сообщение об ошибке сбора: причина и одна команда для проверки."""
    moment = (now or datetime.now(SAMARA)).astimezone(SAMARA)
    lines = [
        "Ежедневный отчёт не собран",
        f"Доска {board}, {moment:%d.%m.%Y %H:%M}",
        "",
        f"Причина: {reason}",
        "Проверить: env -u PYTHONPATH .venv/bin/python "
        "scripts/cron/kanban_daily.py --dry-run",
    ]
    return "\n".join(lines)[:LIMIT]


def deliver(text: str, dry_run: bool = False) -> int:
    """Отправляет текст тем же каналом, что и остальные сводки проекта.

    В `--dry-run` текст печатается как есть, без обращения к Telegram: ручная
    проверка не должна зависеть от наличия токена, а сравнить напечатанное с
    ушедшим в чат можно побайтно.
    """
    if dry_run:
        print(text)
        return 0
    return notify_admin.main([text])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ежедневный отчёт «требует вас»")
    parser.add_argument("--board", default=BOARD)
    parser.add_argument("--hermes-home", default=None,
                        help="каталог Hermes с досками (по умолчанию ~/.hermes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="напечатать текст и не отправлять")
    args = parser.parse_args(argv)

    try:
        data = collect(find_db(args.board, args.hermes_home))
    except CollectionError as exc:
        code = deliver(format_error(str(exc), args.board), args.dry_run)
        # 1 даже при удачной отправке: сломанный сбор должен быть виден в cron.
        return code or 1

    return deliver(format_report(data, args.board), args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

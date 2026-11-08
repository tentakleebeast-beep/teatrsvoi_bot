#!/usr/bin/env python3
"""Очередь карт канбана: порядок диспетчера и объяснение первой карты.

Зачем: `priority` — число, а число без определения каждый понимает по-своему.
Одна карта получала 5 «за безопасность», другая 5 «потому что важная», тройка
означала всё сразу. Правила классов — docs/development/26 §9. Этот скрипт
показывает, что из этих чисел следует на самом деле: диспетчер берёт карты
по `priority DESC, created_at ASC`, и скрипт печатает ровно этот порядок,
а не своё мнение о важности.

Что печатает:
  очередь      — карты в статусах ready/todo, в порядке диспетчера;
  вне очереди  — blocked, scheduled, running, review (приоритет виден, но
                 диспетчер их не берёт);
  следующая    — первая карта, которую возьмёт диспетчер, и почему именно она;
  без класса   — карты, у которых priority = 0, пустой или вне набора {1..5}.

  env -u PYTHONPATH .venv/bin/python scripts/kanban_queue.py
  env -u PYTHONPATH .venv/bin/python scripts/kanban_queue.py --board teatrsvoi
  env -u PYTHONPATH .venv/bin/python scripts/kanban_queue.py --json
  env -u PYTHONPATH .venv/bin/python scripts/kanban_queue.py --strict

Код выхода: 0 — отчёт напечатан; с --strict 1 — если есть карты без класса.
Без --strict очередь это отчёт, а не проверка: код 0.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import time

# Диспетчер берёт только ready; todo ждёт родителей. Обе — очередь.
QUEUE_STATUSES = ("ready", "todo")
# Карты в работе, у которых приоритет обязан быть классом (границы карты:
# done и архив не трогаем — история не переписывается).
LIVE_STATUSES = ("ready", "todo", "blocked", "scheduled", "running", "review")
OUTSIDE_STATUSES = ("blocked", "scheduled", "running", "review")

CLASS_BY_PRIORITY = {5: "P0", 4: "P1", 3: "P2", 2: "P3", 1: "P4"}
CLASS_TITLES = {
    5: "P0 — безопасность, потеря данных, прод недоступен",
    4: "P1 — сломан пользовательский путь",
    3: "P2 — запланированное развитие",
    2: "P3 — гигиена",
    1: "P4 — «когда-нибудь», без критериев",
}
VALID_PRIORITIES = frozenset(CLASS_BY_PRIORITY)

HOUR = 3600


def boards(home: pathlib.Path, only: str | None) -> list[tuple[str, pathlib.Path]]:
    """Борды Hermes: ~/.hermes/kanban/boards/<slug>/kanban.db."""
    out: list[tuple[str, pathlib.Path]] = []
    base = home / "kanban" / "boards"
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            db = folder / "kanban.db"
            if db.is_file() and (only is None or folder.name == only):
                out.append((folder.name, db))
    legacy = home / "kanban.db"          # старый борд без папки
    if legacy.is_file() and only in (None, "default"):
        out.append(("default", legacy))
    return out


class BoardError(SystemExit):
    """База борда не читается: это не пустая очередь, а сломанный отчёт."""


def read_cards(db: pathlib.Path) -> list[dict]:
    """Карты в работе с полями, которые нужны для порядка и объяснения."""
    marks = ",".join("?" * len(LIVE_STATUSES))
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"select id, title, status, priority, created_at, coalesce(assignee, '') as assignee "
            f"from tasks where status in ({marks})",
            LIVE_STATUSES).fetchall()
    except sqlite3.Error as e:            # база занята/битая — не выдаём пустоту за порядок
        raise BoardError(f"не удалось прочитать {db}: {e}") from e
    finally:
        if conn is not None:
            conn.close()
    return [dict(r) for r in rows]


def has_class(priority: object) -> bool:
    """Приоритет — класс, если это целое из {1,2,3,4,5}; 0 и None — не класс."""
    return isinstance(priority, int) and priority in VALID_PRIORITIES


def class_name(priority: object) -> str | None:
    if not isinstance(priority, int) or priority not in VALID_PRIORITIES:
        return None
    return CLASS_BY_PRIORITY[priority]


def order(cards: list[dict]) -> list[dict]:
    """Порядок диспетчера: priority DESC, created_at ASC (kanban_db_dispatch).

    Карта без класса не может стоять выше осмысленной: считаем её нулём.
    При равном приоритете и возрасте — по id, чтобы вывод был воспроизводим.
    """
    return sorted(cards, key=lambda c: (
        -(c["priority"] if has_class(c["priority"]) else 0),
        c["created_at"],
        c["id"],
    ))


def _card_view(card: dict, now: int) -> dict:
    age_h = max(0.0, round((now - card["created_at"]) / HOUR, 1))
    return {
        "id": card["id"],
        "title": card["title"],
        "status": card["status"],
        "priority": card["priority"],
        "class": class_name(card["priority"]),
        "assignee": card["assignee"],
        "created_at": card["created_at"],
        "age_h": age_h,
    }


def pick_next(queue: list[dict]) -> dict | None:
    """Первая карта, которую реально возьмёт диспетчер: ready + исполнитель."""
    for card in queue:
        if card["status"] == "ready" and card["assignee"].strip() and has_class(card["priority"]):
            return card
    return None


def why_next(card: dict, queue: list[dict], now: int) -> str:
    """Объяснение выбора первой карты — числами, а не «так важнее»."""
    priority = card["priority"]
    cls = class_name(priority) or "?"
    same = [c for c in queue
            if c["status"] == "ready" and has_class(c["priority"])
            and c["priority"] == priority]
    older = [c for c in same if c["created_at"] < card["created_at"]]
    higher = [c for c in queue
              if c["status"] == "ready" and has_class(c["priority"])
              and c["priority"] > priority]
    why = f"{cls} ({priority}) — {CLASS_TITLES[priority].split(' — ', 1)[1]}"
    if higher:
        why += f"; выше по приоритету {len(higher)} карт(ы), но они не готовы к запуску"
    else:
        why += "; карт с более высоким приоритетом в ready нет"
    if len(same) > 1:
        if older:
            why += (f"; в {cls} {len(same)} карт(ы), эта старше на {len(older)}"
                    " — при равном приоритете диспетчер берёт старшую")
        else:
            why += (f"; в {cls} {len(same)} карт(ы), эта старше остальных"
                    " — при равном приоритете диспетчер берёт старшую")
    age_h = round((now - card["created_at"]) / HOUR, 1)
    why += f"; ждёт {age_h} ч"
    return why


def build_report(cards: list[dict], now: int) -> dict:
    ordered = order(cards)
    queue = [_card_view(c, now) for c in ordered if c["status"] in QUEUE_STATUSES]
    outside = [_card_view(c, now) for c in ordered if c["status"] in OUTSIDE_STATUSES]
    unclassified = [_card_view(c, now) for c in ordered if not has_class(c["priority"])]
    first = pick_next(queue)
    counts = {str(p): 0 for p in sorted(VALID_PRIORITIES, reverse=True)}
    for card in cards:
        if has_class(card["priority"]):
            counts[str(card["priority"])] += 1
    return {
        "generated_at": now,
        "order": "priority DESC, created_at ASC",
        "next": ({**_card_view(first, now), "why": why_next(first, queue, now)}
                 if first is not None else None),
        "queue": queue,
        "outside": outside,
        "unclassified": unclassified,
        "priority_counts": counts,
        "ok": not unclassified,
        "classes": {str(p): {"name": CLASS_BY_PRIORITY[p], "title": CLASS_TITLES[p]}
                    for p in sorted(VALID_PRIORITIES, reverse=True)},
    }


def _fmt_card(index: int, card: dict) -> str:
    cls = card["class"] or "—"
    return (f"{index:>2}  {cls:<2} {card['priority'] if card['class'] else '?':<2} "
            f"{card['age_h']:>6.1f} ч  {card['status']:<9} {card['id']}  {card['title']}")


def print_report(slug: str, report: dict) -> None:
    print(f"Очередь борда {slug}: порядок диспетчера — {report['order']}")
    print()
    if report["queue"]:
        print("Очередь (ready — берёт диспетчер, todo — ждёт родителей):")
        for i, card in enumerate(report["queue"], 1):
            print(_fmt_card(i, card))
    else:
        print("Очередь пуста: карт в ready и todo нет.")
    if report["outside"]:
        print()
        print("Вне очереди (приоритет виден, но диспетчер эти карты не берёт):")
        for card in report["outside"]:
            print(f"    {card['class'] or '—':<2} {card['priority'] if card['class'] else '?':<2} "
                  f"{card['age_h']:>6.1f} ч  {card['status']:<9} {card['id']}  {card['title']}")
    print()
    if report["next"]:
        nxt = report["next"]
        print(f"Следующая: {nxt['id']} — {nxt['title']}")
        print(f"Почему: {nxt['why']}")
    else:
        print("Следующей нет: в ready нет карты с исполнителем и классом приоритета.")
    if report["unclassified"]:
        print()
        print(f"Без класса ({len(report['unclassified'])}): приоритет 0, пустой или вне {{1..5}} —"
              " поставить класс по docs/development/26 §9:")
        for card in report["unclassified"]:
            print(f"    {card['status']:<9} {card['id']}  {card['priority']!r}  {card['title']}")
    human = ", ".join(f"{CLASS_BY_PRIORITY[int(p)]} ({p}): {n}"
                      for p, n in report["priority_counts"].items())
    print()
    print(f"Карт в работе: {len(report['queue']) + len(report['outside'])}; по классам — {human}.")


def kanban_home() -> pathlib.Path:
    """Корень Hermes с доской: HERMES_HOME, иначе ~/.hermes.

    У профиля-воркера HERMES_HOME = ~/.hermes/profiles/<profile>, а доска
    лежит в общем ~/.hermes/kanban/boards. Без этой развилки «одна команда»
    в воркере отвечала бы «борды не найдены» при живой доске.
    """
    candidates = [os.getenv("HERMES_HOME"), str(pathlib.Path.home() / ".hermes")]
    for candidate in candidates:
        if candidate and (pathlib.Path(candidate) / "kanban" / "boards").is_dir():
            return pathlib.Path(candidate)
    return pathlib.Path(os.getenv("HERMES_HOME") or pathlib.Path.home() / ".hermes")


def main() -> int:
    p = argparse.ArgumentParser(description="Очередь карт канбана в порядке диспетчера")
    p.add_argument("--board", default=None, help="только этот борд (по умолчанию все)")
    p.add_argument("--json", action="store_true", help="машинный вывод")
    p.add_argument("--strict", action="store_true",
                   help="код 1, если есть карты без класса приоритета")
    args = p.parse_args()

    home = kanban_home()
    targets = boards(home, args.board)
    if not targets:
        print(f"Борды не найдены в {home / 'kanban' / 'boards'}")
        return 1

    now = int(time.time())
    reports = []
    for slug, db in targets:
        report = {"board": slug, **build_report(read_cards(db), now)}
        reports.append(report)

    if args.json:
        payload = reports[0] if len(reports) == 1 else {"boards": reports}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for i, report in enumerate(reports):
            if i:
                print()
            print_report(report["board"], report)

    if args.strict and not all(r["ok"] for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Метрика канбан-конвейера: не ощущения, а цифры.

Зачем: после волны 31.10–01.11 на доске было 19 закрытых карт, 2 доводки и одна
поломка, дожившая до прода. Без цифр нельзя понять, стало ли лучше от правил
(гейт, скиллы воркера, ревьюер) — каждая правка выглядит «наверное, помогает».

Что считаем:
  выполнено       — карты в статусе done;
  цикл            — медиана часов от создания до завершения;
  прогоны         — сколько прогонов в среднем нужно на карту (больше двух — шум);
  авралы          — timed_out и crashed: бюджет итераций и падения воркеров;
  доводки         — карты, которые ревью вернуло (changes_requested);
  после           — карты, заведённые другой картой (creator_task_id): признак того,
                    что работа потребовала хвоста после закрытия;
  очередь         — сколько карт ждёт и сколько самая старая ждёт.

  env -u PYTHONPATH .venv/bin/python scripts/kanban_metrics.py
  env -u PYTHONPATH .venv/bin/python scripts/kanban_metrics.py --board teatrsvoi --json

Код выхода всегда 0: это отчёт, а не проверка.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import time

HOUR = 3600


def _boards(home: pathlib.Path, only: str | None) -> list[tuple[str, pathlib.Path]]:
    out: list[tuple[str, pathlib.Path]] = []
    base = home / "kanban" / "boards"
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            db = folder / "kanban.db"
            if db.is_file() and (only is None or folder.name == only):
                out.append((folder.name, db))
    legacy = home / "kanban.db"
    if legacy.is_file() and only in (None, "default"):
        out.append(("default", legacy))
    return out


def collect(db: pathlib.Path, days: int) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = int(time.time()) - days * 24 * HOUR
    data: dict = {}

    rows = conn.execute("select status, created_at, completed_at, started_at from tasks").fetchall()
    done = [r for r in rows if r["status"] == "done" and r["completed_at"]]
    data["done"] = len(done)
    data["done_in_window"] = sum(1 for r in done if r["completed_at"] >= since)

    cycles = [(r["completed_at"] - r["created_at"]) / HOUR for r in done if r["completed_at"] > r["created_at"]]
    data["cycle_median_h"] = round(statistics.median(cycles), 1) if cycles else None

    # Прогоны считаем за то же окно, что и «выполнено за N дней»: иначе в отчёте
    # рядом стоят числа за разные периоды и сравнение недель врёт.
    runs = conn.execute(
        "select task_id, outcome, started_at from task_runs where started_at >= ?", (since,)
    ).fetchall()
    per_card: dict[str, int] = {}
    for r in runs:
        per_card[r["task_id"]] = per_card.get(r["task_id"], 0) + 1
    data["runs_total"] = len(runs)
    data["runs_per_card"] = round(sum(per_card.values()) / len(per_card), 2) if per_card else None
    data["timeouts"] = sum(1 for r in runs if r["outcome"] == "timed_out")
    data["crashes"] = sum(1 for r in runs if r["outcome"] == "crashed")
    data["rework"] = sum(1 for r in runs if r["outcome"] == "changes_requested")
    data["rework_rate"] = round(data["rework"] / max(1, data["done"]), 2)

    # Дети карт делятся на два разных класса, и путать их нельзя (урок 01.11.2026):
    #   разбиение  — ребёнок создан ДО закрытия родителя: это план, работа шла вместе;
    #   хвост      — ребёнок создан ПОСЛЕ закрытия: родитель уехал в done, а доделывать
    #                пришлось отдельной картой. Вот это и есть мера «недоделали».
    # Раньше оба считались «хвостами», и метрика показывала 0.52 при фактических нулях.
    kids = conn.execute(
        "select c.task_id as child, c.created_at as born, "
        "  (select completed_at from tasks where id = json_extract(c.payload, '$.creator_task_id')) as closed "
        "from task_events c "
        "where c.kind = 'created' "
        "  and json_extract(c.payload, '$.creator_task_id') is not null").fetchall()
    tails = [k for k in kids if k["closed"] and k["born"] > k["closed"]]
    data["children_cards"] = len(kids)
    data["decomposed"] = len(kids) - len(tails)
    data["tails_after_close"] = len(tails)
    data["tail_rate"] = round(len(tails) / max(1, data["done"]), 2)
    data["tails"] = [
        {"id": k["child"],
         "title": conn.execute("select title from tasks where id = ?", (k["child"],)).fetchone()[0]}
        for k in tails
    ]

    # Дубли: две карты про одно и то же — тоже потеря работы (сегодня было две сводки).
    dup = conn.execute(
        "select count(*) from (select lower(trim(title)) as t from tasks "
        "group by t having count(*) > 1)").fetchone()[0]
    data["duplicate_titles"] = dup

    open_rows = conn.execute(
        "select id, title, status, created_at from tasks "
        "where status in ('ready','todo','blocked','running','review')").fetchall()
    # Список очереди нужен сводке: «13 в очереди» без имён ничего не объясняет.
    data["queue"] = [
        {"id": r["id"], "title": r["title"], "status": r["status"],
         "age_h": round((time.time() - r["created_at"]) / HOUR, 1)}
        for r in sorted(open_rows, key=lambda x: x["created_at"])
    ]
    data["open"] = len(open_rows)
    data["oldest_open_h"] = (round((time.time() - min(r["created_at"] for r in open_rows)) / HOUR, 1)
                             if open_rows else None)
    data["blocked"] = sum(1 for r in open_rows if r["status"] == "blocked")
    conn.close()
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Метрика канбан-конвейера")
    parser.add_argument("--board", default=None, help="только этот борд")
    parser.add_argument("--days", type=int, default=7, help="окно для «выполнено за период»")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    home = pathlib.Path(os.getenv("HERMES_HOME") or pathlib.Path.home() / ".hermes")
    result = {}
    for slug, db in _boards(home, args.board):
        try:
            result[slug] = collect(db, args.days)
        except sqlite3.Error as e:
            result[slug] = {"error": str(e)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for slug, m in result.items():
        print(f"\nБорд {slug}")
        if "error" in m:
            print(f"  ошибка чтения: {m['error']}")
            continue
        def line(name, value, note=""):
            print(f"  {name:<26} {value}{('  ' + note) if note else ''}")
        line("выполнено всего", m["done"])
        line(f"выполнено за {args.days} дн", m["done_in_window"])
        line("цикл (медиана)", f"{m['cycle_median_h']} ч" if m["cycle_median_h"] else "—")
        line("прогонов на карту", m["runs_per_card"] if m["runs_per_card"] else "—")
        line("авралы (таймаут/падение)", f"{m['timeouts']} / {m['crashes']}")
        line("доводки ревью", f"{m['rework']} (на карту: {m['rework_rate']})")
        line("разбиение плана", m["decomposed"])
        line("хвосты после закрытия", f"{m['tails_after_close']} (на карту: {m['tail_rate']})")
        line("дубли названий", m["duplicate_titles"])
        line("в очереди", f"{m['open']}, блокировано {m['blocked']}")
        line("самая старая в очереди", f"{m['oldest_open_h']} ч" if m["oldest_open_h"] else "—")
    return 0


if __name__ == "__main__":
    sys.exit(main())

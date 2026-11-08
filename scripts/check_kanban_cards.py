#!/usr/bin/env python3
"""Проверка карт канбана: пустых карт и двух карт на один файл в работе быть не должно.

Правило владельца (31.10.2026, docs/development/26 §7a): в каждой карте есть описание —
что меняем и зачем, область, критерии приёмки, границы. Карта без описания стоит воркеру
целого захода: спецификация теряется, задача стартует без контекста.

Второе правило (01.11.2026, редакция 04.11.2026 — docs/development/26 §7b): в работе
не больше одной карты на **файл**. Две карты, объявившие один и тот же существующий файл,
дают конфликт слияния; карты с разными файлами в одном каталоге конфликта не дают.
Файлы берутся из раздела «Область» скриптом `scripts/kanban_areas.py`; сгенерированные
(`docs/README.md`, `contracts/openapi.json`) не блокируют, а помечаются «пересобрать».

  env -u PYTHONPATH .venv/bin/python scripts/check_kanban_cards.py
  env -u PYTHONPATH .venv/bin/python scripts/check_kanban_cards.py --board teatrsvoi
  env -u PYTHONPATH .venv/bin/python scripts/check_kanban_cards.py --json

Проверяются карты в работе: ready, todo, scheduled, running, review, blocked.
Архив и done не проверяются: история не переписывается.

Код выхода: 0 — пустых карт и конфликтов по файлам нет, 1 — есть (по строке на находку),
2 — доска не найдена (проверка не выполнена, это не «всё в порядке»).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from kanban_areas import (  # noqa: E402
    ROOT,
    file_conflicts,
    generated_overlaps,
    hermes_home,
    live_cards,
)

LIVE = ("ready", "todo", "scheduled", "running", "review", "blocked")


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


def empty_cards(db: pathlib.Path) -> list[dict]:
    marks = ",".join("?" * len(LIVE))
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            f"select id, status, coalesce(assignee,'-'), title, "
            f"length(trim(coalesce(body,''))) "
            f"from tasks where status in ({marks}) order by created_at", LIVE).fetchall()
    except sqlite3.Error as e:            # база занята/битая — не валим проверку молча
        raise SystemExit(f"не удалось прочитать {db}: {e}") from e
    finally:
        if conn is not None:
            conn.close()
    return [{"id": r[0], "status": r[1], "assignee": r[2], "title": r[3], "body_len": r[4]}
            for r in rows if not (r[4] or 0)]


def problems(db: pathlib.Path) -> list[dict]:
    """Карты, которые на доске выглядят живыми, но двигаться не будут.

    Два случая, оба видели 01.11.2026:
      - карта в ready/todo без исполнителя: диспетчер такие пропускает, она висит
        вечно и очередь кажется больше, чем есть;
      - blocked без единого комментария: непонятно ни причина, ни что нужно от человека,
        разбирать нечего.
    """
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "select t.id, t.status, coalesce(t.assignee,''), substr(t.title,1,70),"
            "  (select count(*) from task_comments c where c.task_id = t.id)"
            " from tasks t where t.status in ('ready','todo','blocked')"
            " order by t.created_at").fetchall()
    except sqlite3.Error as e:
        raise SystemExit(f"не удалось прочитать {db}: {e}") from e
    finally:
        if conn is not None:
            conn.close()

    out = []
    for task_id, status, assignee, title, comments in rows:
        if status in ("ready", "todo") and not assignee.strip():
            out.append({"id": task_id, "status": status, "title": title,
                        "issue": "нет исполнителя — диспетчер не возьмёт"})
        elif status == "blocked" and not comments:
            out.append({"id": task_id, "status": status, "title": title,
                        "issue": "блокировка без причины — нечего разбирать"})
    return out


def file_clashes(db: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[dict]:
    """Две карты в работе пишут один существующий файл — конфликт слияния на подходе."""
    titles = {card["id"]: card["title"] for card in live_cards(db, repo_root)}
    return [{"files": pair["files"],
             "cards": [{"id": card_id, "title": titles.get(card_id, "")}
                       for card_id in pair["cards"]]}
            for pair in file_conflicts(db, repo_root)]


def rebuild_notes(db: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[dict]:
    """Сгенерированные файлы, объявленные несколько раз: решается пересборкой, не очередью."""
    return generated_overlaps(live_cards(db, repo_root))


def main() -> int:
    p = argparse.ArgumentParser(description="Карты канбана: описание и конфликты по файлам")
    p.add_argument("--board", default=None, help="только этот борд (по умолчанию все)")
    p.add_argument("--json", action="store_true", help="машинный вывод")
    p.add_argument("--hermes-home", default=None,
                   help="корень Hermes с досками (у воркера HERMES_HOME — профиль, без досок)")
    p.add_argument("--repo", default=None,
                   help="корень репозитория для проверки существования файлов")
    args = p.parse_args()

    repo_root = pathlib.Path(args.repo) if args.repo else ROOT
    home = hermes_home(args.hermes_home)
    if not boards(home, args.board):
        print(f"Доска не найдена: {home}/kanban/boards. Проверка не выполнена — "
              f"это не «всё в порядке».", file=sys.stderr)
        return 2
    found: list[dict] = []
    stuck: list[dict] = []
    clashes: list[dict] = []
    rebuild: list[dict] = []
    for slug, db in boards(home, args.board):
        for card in empty_cards(db):
            card["board"] = slug
            found.append(card)
        for card in problems(db):
            card["board"] = slug
            stuck.append(card)
        for clash in file_clashes(db, repo_root):
            clash["board"] = slug
            clashes.append(clash)
        for item in rebuild_notes(db, repo_root):
            item["board"] = slug
            rebuild.append(item)

    if args.json:
        print(json.dumps({"ok": not found and not stuck and not clashes,
                          "empty": found, "stuck": stuck, "conflicts": clashes,
                          "rebuild": rebuild},
                         ensure_ascii=False, indent=2))
        return 1 if (found or stuck or clashes) else 0

    if not found and not stuck and not clashes:
        print("Карты в порядке: у каждой есть описание, у готовых есть исполнитель, "
              "у блокировок — причина, один и тот же файл в работе пишет одна карта.")
        for item in rebuild:
            print(f"  пересобрать: {item['board']}/{item['file']} объявлен у "
                  f"{', '.join(item['cards'])} ({item['generator']})")
        return 0

    if found:
        print(f"Карты без описания: {len(found)}")
        for card in found:
            print(f"  {card['board']}/{card['id']}  {card['status']:<8} {card['assignee']:<16} {card['title']}")
        print("\nПравило: docs/development/26 §7a — карта без описания не заводится.")
    if stuck:
        print(f"\nКарты, которые не поедут: {len(stuck)}")
        for card in stuck:
            print(f"  {card['board']}/{card['id']}  {card['status']:<8} {card['issue']}")
    if clashes:
        print(f"\nДве карты на один файл в работе: {len(clashes)}")
        for clash in clashes:
            names = " и ".join(f"{card['id']} «{card['title'][:50]}»" for card in clash["cards"])
            print(f"  {clash['board']}: {', '.join(clash['files'])} — {names}")
        print("\nПравило: docs/development/26 §7b — один существующий файл в работе "
              "правит одна карта; вторая ждёт. Разбор — scripts/kanban_areas.py.")
    for item in rebuild:
        print(f"  пересобрать: {item['board']}/{item['file']} объявлен у "
              f"{', '.join(item['cards'])} ({item['generator']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())

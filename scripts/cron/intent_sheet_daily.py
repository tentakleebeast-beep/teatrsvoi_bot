#!/usr/bin/env python3
"""Ежедневная выгрузка листа «Чего не умеем» и карточки на спецификацию (V-035).

Задание планировщика — раз в сутки, `no_agent`: скрипт сам решает, нужен ли человек.
Сначала `intent_sheet.py pull`, затем `cards --apply --max-new 5`. Логика здесь,
в репозитории; в `~/.hermes/scripts` лежит только лаунчер (docs/infrastructure/36 §5a).

Правило тишины: новых карточек нет — пустой stdout и код 0. Строк на проде нет (код 4
от `pull`) — тоже тишина: это факт «спрашивать нечего», а не сбой. Отказ по правилу (1),
нет настроек (2) и отказ прода или сети (3) печатают одну строку причины и возвращают
этот код: тишина не должна означать «сломалось».

  env -u PYTHONPATH .venv/bin/python scripts/cron/intent_sheet_daily.py --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/cron/intent_sheet_daily.py
  env -u PYTHONPATH .venv/bin/python scripts/cron/intent_sheet_daily.py --dry-run --board v035-test

`--dry-run` не зовёт `pull` (иначе снимок `.state/intent_sheet.json` перезаписывался бы
вопреки обещанию «ничего не писать») и считает карточки по уже выгруженному снимку.
`--board` уходит в `cards`: без него карточки заводятся на боевой доске из
`intent_sheet.py`, с ним прогон можно повторить на тестовой.

Установка лаунчера и расписания (регистрирует владелец, в репозитории этого нет):

  cat > ~/.hermes/scripts/intent_sheet_daily.sh <<'SH'
  #!/bin/sh
  cd "/Users/viktormamlutov/repos/Projects/teatrsvoi_bot Project" || exit 1
  exec env -u PYTHONPATH .venv/bin/python scripts/cron/intent_sheet_daily.py
  SH
  chmod +x ~/.hermes/scripts/intent_sheet_daily.sh
  hermes cron create "0 9 * * *" --name "лист «Чего не умеем»" \\
      --script intent_sheet_daily.sh --no-agent --deliver local

Тест: tests/unit/test_intent_sheet.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
INTENT = PROJECT / "scripts" / "intent_sheet.py"
MAX_NEW = 5


def run(args: list[str]) -> subprocess.CompletedProcess:
    """Команда конвейера отдельным процессом: у неё свои коды выхода и свой stderr."""
    command = [sys.executable, str(INTENT), *args]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run(command, capture_output=True, text=True, cwd=str(PROJECT),
                          env=env, timeout=600)


def reason(result: subprocess.CompletedProcess) -> str:
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else f"команда упала с кодом {result.returncode}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Лист «Чего не умеем»: выгрузка и карточки")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, какие карточки будут заведены, и ничего не писать")
    parser.add_argument("--board", default=None,
                        help="доска канбана для карточек; по умолчанию — та, что в intent_sheet.py")
    args = parser.parse_args(argv)

    # Сухой прогон обещает «ничего не писать», а `pull` перезаписывает снимок
    # `.state/intent_sheet.json`. Поэтому в `--dry-run` его не зовём вовсе: карточки
    # считаются по уже выгруженному снимку.
    if not args.dry_run:
        pulled = run(["pull"])
        if pulled.returncode == 4:
            return 0                   # строк нет — тишина, не сбой
        if pulled.returncode != 0:
            print(f"лист «Чего не умеем»: выгрузка не удалась — {reason(pulled)}")
            return pulled.returncode

    command = ["cards", "--max-new", str(MAX_NEW), "--json"]
    if not args.dry_run:
        command.insert(1, "--apply")
    if args.board:
        command += ["--board", args.board]
    cards = run(command)
    if cards.returncode != 0:
        print(f"лист «Чего не умеем»: карточки не заведены — {reason(cards)}")
        return cards.returncode

    try:
        summary = json.loads(cards.stdout)
    except ValueError:
        print(cards.stdout.strip())
        return 0
    created = int(summary.get("created") or 0)
    if created == 0:
        return 0                       # новых нет — молчим
    verb = "будет" if args.dry_run else "заведено"
    print(f"лист «Чего не умеем»: новых карточек {verb} {created}")
    for card in summary.get("cards") or []:
        print(f"  {card.get('row_id')} → {card.get('task_id') or 'будет заведена'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

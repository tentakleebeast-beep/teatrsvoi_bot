#!/usr/bin/env python3
"""
Алерт сторожа в очередь уведомлений (Ш-30).

  printf 'Проблемы на сервере SVOI:\n· tool-api не отвечает\n' | \
      .venv/bin/python scripts/watchdog_alert.py
  .venv/bin/python scripts/watchdog_alert.py --problem "диск занят 91%"

Читает строки проблем из stdin (как их печатает `watchdog.sh`) или из `--problem`,
ставит запись `notification` активным админам с дедупликацией «проблема + час» и
печатает итог. Если база недоступна — аварийный резерв: прямая отправка в Telegram.

Коды выхода: 0 — запись в очереди (в том числе уже была), 2 — ушло резервом,
1 — не удалось ни то, ни другое.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _cli import dsn  # noqa: E402

from scripts import alerts  # noqa: E402

TITLE = "Проблемы на сервере SVOI"


def read_problems(tokens: list[str] | None, stdin: str = "") -> list[str]:
    """Проблемы из --problem, иначе из stdin; заголовок и пустые строки отбрасываются."""
    if tokens:
        return alerts.normalize_problems(tokens)
    lines = [line for line in stdin.splitlines()]
    if lines and lines[0].strip().rstrip(":") == TITLE:
        lines = lines[1:]
    return alerts.normalize_problems(lines)


def main(argv: list[str] | None = None, stdin: str = "") -> int:
    parser = argparse.ArgumentParser(description="Алерт сторожа в очередь notification")
    parser.add_argument("--problem", action="append", help="строка проблемы (можно несколько)")
    parser.add_argument("--title", default=TITLE)
    args = parser.parse_args(argv)

    problems = read_problems(args.problem, stdin)
    if not problems:
        print("проблем нет")
        return 0
    try:
        queued = alerts.queue_admins(problems, args.title, datetime.now(UTC), db=dsn())
        print(f"в очередь: {queued}")
        return 0
    except Exception as exc:                           # noqa: BLE001 — база может быть недоступна
        body = alerts.alert_body(args.title, problems)
        sent = alerts.send_telegram(body)
        print(f"очередь недоступна ({exc}); резерв: {'отправлено' if sent else 'не отправлено'}",
              file=sys.stderr)
        return 2 if sent else 1


if __name__ == "__main__":
    # stdin идёт в stdin, а не в argv: строка, переданная как argv, разбирается
    # argparse по символам («unrecognized arguments: '·' ' ' 'н' …») — так сторож
    # молча не доставлял ни одного алерта (найдено 31.10.2026 на проде).
    sys.exit(main(argv=sys.argv[1:], stdin=sys.stdin.read()))

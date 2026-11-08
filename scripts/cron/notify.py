#!/usr/bin/env python3
"""Планирование и отправка напоминаний teatrsvoi_bot. Молчит, если отправлять нечего.

Логика задания планировщика живёт в репозитории. В ~/.hermes/scripts остаётся
тонкий лаунчер: Hermes-cron принимает только имена скриптов внутри своего
каталога, абсолютные пути отклоняет (docs/infrastructure/36 §5).
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("SVOI_PROJECT", Path(__file__).resolve().parents[2]))
PY = str(PROJECT / ".venv" / "bin" / "python")
ENV = {"PYTHONPATH": "", "PATH": "/usr/bin:/bin:/usr/local/bin"}


def run(args):
    return subprocess.run([PY] + args, capture_output=True, text=True, cwd=str(PROJECT), env=ENV)


def main():
    plan = run(["scripts/plan_reminders.py", "--apply"])
    if plan.returncode != 0:
        print(f"планирование напоминаний упало: {(plan.stderr or '')[-300:]}", file=sys.stderr)
        return 1

    send = run(["scripts/send_notifications.py", "--apply"])
    out = (send.stdout or "").strip()
    if send.returncode != 0:
        print(f"отправка упала: {(send.stderr or '')[-300:]}", file=sys.stderr)
        return 1

    # молчим, когда отправлять было нечего: cron не должен спамить
    if "отправлено 0" in out and "ошибок 0" in out:
        return 0
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

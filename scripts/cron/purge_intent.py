#!/usr/bin/env python3
"""Чистка журнала интентов старше 90 дней (docs/21). Молчит, если чистить нечего.

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


def read_env(path):
    """KEY=VALUE из .env: комментарий в значении режется по " #", как в shell."""
    env = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.split(" #", 1)[0].strip()
    except FileNotFoundError:
        pass
    return env


def main():
    env = {"PYTHONPATH": "", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    env.update(read_env(PROJECT / ".env"))
    r = subprocess.run(
        [PY, "scripts/intent_report.py", "--purge"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT),
        env=env,
    )
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        err = (r.stderr or "").strip()[-300:]
        print(f"ошибка чистки журнала: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

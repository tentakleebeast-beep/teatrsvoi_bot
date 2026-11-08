#!/usr/bin/env python3
"""
Health-агент: сверяет живую сборку с HEAD и пишет админу в очередь (FR-H12/H13, L-005).

  python scripts/health_agent.py                 # проверить и напечатать
  python scripts/health_agent.py --apply         # ещё и поставить уведомление в очередь
  python scripts/health_agent.py --url http://127.0.0.1:8000 --root /opt/svoi

Почему отдельно от watchdog: watchdog отвечает на вопрос «процесс жив», агент — на
вопрос «а та ли это сборка». 28.10.2026 три дефекта записали на старую сборку,
потому что версию живого процесса никто не сверял. Сравниваем `git_sha` из
`/health` с `git rev-parse HEAD` в рабочей папке; расхождение — дефект выката.

Уведомление не отправляется напрямую: пишется в `notification`, оттуда его заберёт
`svoi-notify-send.timer` с тихими часами и лимитами. Дедупликация — проблема + час,
чтобы лежащий сервис не завалил админа сообщениями каждые 5 минут.
Код выхода 1 при любой проблеме — годится для таймера и для CI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from _cli import dsn

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_URL = os.getenv("TOOL_API_URL", "http://127.0.0.1:8000")
DEFAULT_ROOT = os.getenv("SVOI_ROOT", str(PROJECT))
HEX = set("0123456789abcdef")


def normalize_sha(value: object) -> str:
    """sha к короткому виду (12 знаков, нижний регистр); мусор → пустая строка."""
    text = str(value or "").strip().lower()
    if len(text) < 7 or any(c not in HEX for c in text):
        return ""
    return text[:12]


def head_sha(root: str | Path) -> str:
    """HEAD рабочей папки. Нет git или ошибка — пустая строка, а не исключение."""
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return normalize_sha(done.stdout) if done.returncode == 0 else ""


def fetch_health(url: str, timeout: float = 10.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def problems(health: dict | None, head: str) -> list[str]:
    """Что не так с живым процессом. Пустой список — всё в порядке."""
    if health is None:
        return ["tool-api не отвечает на /health"]
    found: list[str] = []
    if not health.get("ok"):
        found.append("tool-api ответил ok=false")
    live = normalize_sha(health.get("git_sha"))
    if not live:
        found.append("живой процесс не сообщает git_sha (сборка неизвестна)")
    elif head and live != head:
        found.append(f"живая сборка {live} не совпадает с HEAD {head}")
    if not health.get("started_at"):
        found.append("в /health нет started_at")
    uptime = health.get("uptime")
    if not isinstance(uptime, int) or uptime < 0:
        found.append("в /health нет корректного uptime")
    return found


def alert_text(found: list[str], health: dict | None) -> str:
    lines = ["Проблемы с tool-api SVOI:"]
    lines += [f"· {item}" for item in found]
    if health:
        lines.append(f"сборка: {health.get('git_sha') or 'неизвестна'}, "
                     f"uptime: {health.get('uptime')} с")
    return "\n".join(lines)


def queue_admins(found: list[str], health: dict | None, moment: datetime | None = None) -> int:
    """Уведомление активным админам; возвращает число ставших в очередь записей.

    Очередь и ключ дедупликации общие со сторожем (`scripts/alerts.py`), иначе один и
    тот же сбой приходил бы дважды: от сторожа и от агента.
    """
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    from scripts import alerts

    return alerts.queue_admins(found, "Сбой tool-api", moment,
                               db=dsn(), body=alert_text(found, health))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка живой сборки tool-api с HEAD")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true", help="писать уведомление в очередь")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    health = fetch_health(args.url, args.timeout)
    head = head_sha(args.root)
    found = problems(health, head)

    print(f"адрес: {args.url}, HEAD: {head or 'неизвестен'}")
    if not found:
        print(f"в порядке: сборка {normalize_sha(health.get('git_sha'))}, "
              f"uptime {health.get('uptime')} с")
        return 1 if not head else 0

    print("\n".join(found))
    if args.apply:
        try:
            queued = queue_admins(found, health)
            print(f"в очередь админам: {queued}")
        except Exception as exc:                     # noqa: BLE001 — агент не должен падать молча
            print(f"очередь недоступна: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

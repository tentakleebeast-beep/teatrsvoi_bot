#!/usr/bin/env python3
"""Еженедельный замер дрейфа: карта-отчёт техлиду, тишина когда всё в норме.

Задание планировщика — понедельник 10:00 (после сводки конвейера), `no_agent`:
скрипт сам решает, нужен ли человек. Логика здесь, в репозитории; в `~/.hermes/scripts`
лежит только лаунчер (docs/infrastructure/36 §5a).

Правило: `scripts/arch_drift.py --json` даёт метрики; если хоть одна за порогом —
на доске `teatrsvoi` заводится карта с assignee `architect`, в теле числа. Если всё
в норме — пустой stdout и код 0: молчание означает «дрейфа нет», а не «замер сломался»
(сломанный замер печатает причину и отдаёт код 1).

Карта идемпотентна по дате: повторный прогон в тот же день не заводит вторую.

  env -u PYTHONPATH .venv/bin/python scripts/cron/arch_drift_weekly.py --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/cron/arch_drift_weekly.py \\
      --threshold cross_service_imports=-1 --dry-run     # сухой прогон с заниженным порогом

Тест: tests/unit/test_arch_drift_weekly.py.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from datetime import timedelta, timezone

PROJECT = pathlib.Path(__file__).resolve().parents[2]
BOARD = "teatrsvoi"
ASSIGNEE = "architect"
PRIORITY = 3
SAMARA = timezone(timedelta(hours=4))
HERMES = pathlib.Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"


def run_drift(root: pathlib.Path = PROJECT, thresholds: tuple[str, ...] = (),
              python: str | None = None) -> dict:
    """Замер дрейфа: JSON из arch_drift.py. Падение замера — исключение, не тишина."""
    command = [python or sys.executable, str(root / "scripts" / "arch_drift.py"),
               "--json", "--root", str(root)]
    for item in thresholds:
        command += ["--threshold", item]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(root),
                            env=env, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"замер дрейфа упал (код {result.returncode}): "
                           f"{(result.stderr or result.stdout).strip()[-500:]}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("замер дрейфа вернул не JSON: "
                           f"{result.stdout.strip()[:200]}") from None


def over_metrics(data: dict) -> list[dict]:
    """Метрики за порогом: только они и есть повод для карты."""
    return [metric for metric in data.get("metrics", []) if metric.get("status") == "warn"]


def plural(count: int) -> str:
    """«1 метрика / 2 метрики / 5 метрик» — карту читает человек."""
    if count % 10 == 1 and count % 100 != 11:
        return "метрика"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "метрики"
    return "метрик"


def card_title(data: dict, count: int) -> str:
    return f"Дрейф архитектуры: {count} {plural(count)} за порогом ({data['date']})"


def card_body(data: dict) -> str:
    """Тело карты: числа, пути, команда. Без предложений — их пишет техлид в отчёте."""
    lines = [
        f"Замер дрейфа архитектуры от {data['date']}, коммит {data['commit']}.",
        "",
        "| Метрика | Сейчас | Порог | Что значит |",
        "|---|---|---|---|",
    ]
    for metric in over_metrics(data):
        value = "ошибка" if metric.get("value") is None else metric["value"]
        lines.append(f"| {metric['title']} (`{metric['id']}`) | {value} | "
                     f"{metric['threshold']} | {metric.get('note') or '—'} |")
    lines += ["", "Подробности:"]
    for metric in over_metrics(data):
        for item in metric.get("details", [])[:8]:
            lines.append(f"- {metric['title']}: {item}")
    lines += [
        "",
        "Команда замера: `env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py`",
        "Отчёт с ценой предложений: `scripts/arch_drift.py --write-report "
        "--proposals <файл>`; решения по каждому пункту принимает владелец.",
        "",
        "Это отчёт, а не гейт: замер ничего не чинит и ничего не блокирует.",
    ]
    return "\n".join(lines)


def create_card(title: str, body: str, key: str, board: str = BOARD,
                hermes: pathlib.Path = HERMES) -> tuple[int, str]:
    """Карта на доске через `hermes kanban create`; ключ идемпотентности — дата замера."""
    if not hermes.is_file():
        return 1, f"нет hermes CLI: {hermes}"
    command = [str(hermes), "kanban", "--board", board, "create", title,
               "--body", body, "--assignee", ASSIGNEE, "--priority", str(PRIORITY),
               "--idempotency-key", key, "--json"]
    env = {key_: value for key_, value in os.environ.items() if key_ != "HERMES_HOME"}
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
    if result.returncode != 0:
        return 1, f"карта не заведена (код {result.returncode}): " \
                  f"{(result.stderr or result.stdout).strip()[-500:]}"
    task_id = ""
    try:
        task_id = str(json.loads(result.stdout).get("id") or "")
    except ValueError:
        pass
    return 0, task_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Еженедельный замер дрейфа архитектуры")
    parser.add_argument("--root", default=str(PROJECT))
    parser.add_argument("--board", default=BOARD)
    parser.add_argument("--dry-run", action="store_true",
                        help="напечатать карту и не заводить её")
    parser.add_argument("--threshold", action="append", default=[],
                        help="занизить порог для сухого прогона: id=число")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    try:
        data = run_drift(root, tuple(args.threshold))
    except RuntimeError as exc:
        print(f"Замер дрейфа не удался: {exc}")
        return 1

    over = over_metrics(data)
    if not over:
        return 0                                   # тишина: дрейфа нет

    title = card_title(data, len(over))
    body = card_body(data)
    if args.dry_run:
        print(title)
        print()
        print(body)
        return 0

    code, detail = create_card(title, body, key=f"arch-drift-{data['date']}", board=args.board)
    if code != 0:
        print(f"Дрейф архитектуры: {len(over)} метрик за порогом, но {detail}")
        return 1
    print(f"Дрейф архитектуры: {len(over)} метрик за порогом — карта {detail or '(id не получен)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

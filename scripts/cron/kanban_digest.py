#!/usr/bin/env python3
"""Недельная сводка канбан-конвейера в Telegram владельца.

Пять чисел, по которым видно, стало лучше или хуже: выполнено за неделю,
цикл (медиана), доводки ревью, аварии, очередь. Источник цифр — существующий
`scripts/kanban_metrics.py --json`; своей логики сбора здесь нет.

  env -u PYTHONPATH .venv/bin/python scripts/cron/kanban_digest.py --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/cron/kanban_digest.py

Код выхода: 0 — сводка собрана и отправлена; 1 — сбор не удался или Telegram
отказал; 2 — нет токена/адресата. Ошибка сбора не глотается: она уходит в чат
текстом, иначе неделя тишины выглядит как «всё хорошо».

Ловушка HERMES_HOME: воркер канбана работает с HERMES_HOME профиля, и метрика
тогда ищет доску в `<профиль>/kanban/boards` — там её нет, JSON приходит `{}`.
Поэтому переменная перед вызовом снимается (или задаётся явно `--hermes-home`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "scripts"))

import notify_admin  # noqa: E402

METRICS = PROJECT / "scripts" / "kanban_metrics.py"
BOARD = "teatrsvoi"
DAYS = 7
SAMARA = timezone(timedelta(hours=4))  # UTC+4, без перехода на летнее время
LIMIT = 700          # одна экранная страница; Telegram режет на 4096
TIMEOUT = 120


class CollectionError(Exception):
    """Собрать цифры не удалось — сообщаем причину текстом, а не молчим."""


def collect(board: str = BOARD, days: int = DAYS,
            hermes_home: str | None = None) -> dict:
    """Запускает kanban_metrics.py и возвращает метрики доски.

    HERMES_HOME снимается: иначе в профиле воркера метрика молча отдаёт `{}`.
    """
    env = dict(os.environ)
    env.pop("HERMES_HOME", None)
    env.pop("PYTHONPATH", None)
    if hermes_home:
        env["HERMES_HOME"] = str(hermes_home)

    try:
        proc = subprocess.run(
            [sys.executable, str(METRICS), "--board", board,
             "--days", str(days), "--json"],
            capture_output=True, text=True, env=env, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectionError(f"метрика не запустилась: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "без вывода"
        raise CollectionError(f"сбор цифр упал (код {proc.returncode}): {tail[:200]}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CollectionError(f"метрика вернула не JSON: {exc}") from exc

    return pick_board(payload, board)


def pick_board(payload: dict, board: str = BOARD) -> dict:
    """Достаёт метрики одной доски и отсекает «нет данных» как ошибку."""
    data = payload.get(board) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise CollectionError(
            f"доска {board} не найдена: метрика вернула {list(payload or {}) or 'пусто'}")
    if "error" in data:
        raise CollectionError(f"база доски недоступна: {data['error']}")
    if not data.get("done") and not data.get("open"):
        raise CollectionError("на доске нет карт")
    return data


def _num(value, unit: str = "", empty: str = "—") -> str:
    """Число по-русски: None — прочерк, целое без хвоста .0."""
    if value is None:
        return empty
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{unit}"


def format_digest(data: dict, board: str = BOARD, days: int = DAYS,
                  now: datetime | None = None) -> str:
    """Текст сводки: заголовок и пять блоков, без эмодзи и вложений."""
    moment = (now or datetime.now(SAMARA)).astimezone(SAMARA)
    cycle = _num(data.get("cycle_median_h"), " ч")
    runs = _num(data.get("runs_per_card"))
    rework_rate = _num(data.get("rework_rate"))
    oldest = _num(data.get("oldest_open_h"), " ч")

    lines = [
        "Недельная сводка конвейера",
        f"Доска {board}, окно {days} дн, {moment:%d.%m.%Y %H:%M}",
        "",
        f"Выполнено за неделю: {_num(data.get('done_in_window'))} "
        f"(всего закрыто {_num(data.get('done'))})",
        f"Цикл (медиана): {cycle}, прогонов на карту: {runs}",
        f"Доводки ревью: {_num(data.get('rework'))}, на карту: {rework_rate}",
        f"Аварии (таймаут/падение): {_num(data.get('timeouts'))} / "
        f"{_num(data.get('crashes'))}",
        f"В очереди / блокировано: {_num(data.get('open'))} / "
        f"{_num(data.get('blocked'))}, самая старая ждёт {oldest}",
    ]
    text = "\n".join(lines)
    return text[:LIMIT]


def format_error(reason: str, board: str = BOARD,
                 now: datetime | None = None) -> str:
    """Сообщение об ошибке сбора: причина и одна команда для проверки."""
    moment = (now or datetime.now(SAMARA)).astimezone(SAMARA)
    text = "\n".join([
        "Недельная сводка конвейера не собрана",
        f"Доска {board}, {moment:%d.%m.%Y %H:%M}",
        "",
        f"Причина: {reason}",
        "Проверить: env -u PYTHONPATH .venv/bin/python "
        f"scripts/kanban_metrics.py --board {board} --json",
    ])
    return text[:LIMIT]


def deliver(text: str, dry_run: bool = False) -> int:
    """Отправляет текст тем же каналом, что и остальные сводки проекта."""
    return notify_admin.main((["--dry-run"] if dry_run else []) + [text])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Недельная сводка канбан-конвейера")
    parser.add_argument("--board", default=BOARD)
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--hermes-home", default=None,
                        help="каталог Hermes с досками (по умолчанию ~/.hermes)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        data = collect(args.board, args.days, args.hermes_home)
    except CollectionError as exc:
        code = deliver(format_error(str(exc), args.board), args.dry_run)
        # 1 даже при удачной отправке: сломанный сбор должен быть виден в cron.
        return code or 1

    return deliver(format_digest(data, args.board, args.days), args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Отступление после лимита провайдера и потолок прогонов на день.

Зачем: 04.11.2026 восемь карт имели по 92–96 прогонов за 12 часов. Каждый стартовал,
падал на rate_limited, сторож через 10 минут возвращал карту, диспетчер снова её
поднимал — 767 прогонов впустую и сожжённая квота.

Что делает:
  --mark     карте с последним исходом rate_limited назначает паузу (1, 2, 5, 15, 60 мин)
             и при пятой неудаче уводит её к человеку;
  --release  возвращает в очередь карты, у которых пауза истекла;

Состояние — файл JSON рядом с другими отметками сторожа: схему доски не трогаем.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import subprocess
import sys
import time

BACKOFF = (60, 120, 300, 900, 3600)   # секунды между попытками
MAX_FAILS = 5                          # дальше — к человеку
HOLD_REASON = "лимит провайдера"


def _connect(db: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def last_outcomes(db: pathlib.Path) -> dict[str, str]:
    """Последний исход прогона по каждой карте."""
    conn = _connect(db)
    try:
        rows = conn.execute(
            "select task_id, outcome from task_runs where id in"
            " (select max(id) from task_runs group by task_id)").fetchall()
    finally:
        conn.close()
    return {task_id: outcome or "" for task_id, outcome in rows}


def live_cards(db: pathlib.Path) -> dict[str, str]:
    conn = _connect(db)
    try:
        rows = conn.execute(
            "select id, status from tasks where status not in ('done','archived')").fetchall()
    finally:
        conn.close()
    return dict(rows)


def load(path: pathlib.Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save(path: pathlib.Path, state: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def plan_marks(db: pathlib.Path, state: dict[str, dict],
               now: int | None = None) -> tuple[list[tuple[str, int]], list[str], dict]:
    """Кому пауза, кого к человеку. Возвращает (пауза, к человеку, новое состояние)."""
    now = now or int(time.time())
    outcomes, cards = last_outcomes(db), live_cards(db)
    pause: list[tuple[str, int]] = []
    to_human: list[str] = []
    fresh: dict[str, dict] = {}
    for task_id, status in cards.items():
        if outcomes.get(task_id) != "rate_limited" or status not in ("ready", "running", "scheduled"):
            continue
        entry = state.get(task_id, {"fails": 0, "next": 0})
        fails = int(entry.get("fails", 0)) + 1
        if fails >= MAX_FAILS:
            to_human.append(task_id)
            continue
        delay = BACKOFF[min(fails - 1, len(BACKOFF) - 1)]
        fresh[task_id] = {"fails": fails, "next": now + delay}
        pause.append((task_id, delay))
    return pause, to_human, fresh


def due(state: dict[str, dict], now: int | None = None) -> list[str]:
    now = now or int(time.time())
    return [task_id for task_id, entry in state.items() if int(entry.get("next", 0)) <= now]


def _kanban(hermes: str, board: str, *args: str) -> bool:
    result = subprocess.run([hermes, "kanban", "--board", board, *args],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0


def main() -> int:
    p = argparse.ArgumentParser(description="Отступление после лимита провайдера")
    p.add_argument("--board", required=True)
    p.add_argument("--hermes-home", default=str(pathlib.Path.home() / ".hermes"))
    p.add_argument("--hermes-bin", default="hermes")
    p.add_argument("--state", default=None)
    p.add_argument("--mark", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    home = pathlib.Path(args.hermes_home)
    db = home / "kanban" / "boards" / args.board / "kanban.db"
    state_path = pathlib.Path(args.state) if args.state else home / "run" / "kanban-backoff.json"
    state = load(state_path)

    if args.release:
        ready_ids = due(state)
        for task_id in ready_ids:
            if args.dry_run:
                print(f"сухо: отпустить {task_id}")
            elif _kanban(args.hermes_bin, args.board, "unblock", task_id):
                print(f"отпустил {task_id}: пауза истекла")
            state.pop(task_id, None)
        save(state_path, state)

    if args.mark:
        pause, to_human, fresh = plan_marks(db, state)
        for task_id, delay in pause:
            reason = f"{HOLD_REASON}: повтор через {delay // 60} мин"
            if args.dry_run:
                print(f"сухо: {task_id} — {reason}")
            elif _kanban(args.hermes_bin, args.board, "schedule", task_id, reason):
                print(f"придержал {task_id}: {reason}")
        for task_id in to_human:
            reason = f"{HOLD_REASON}: {MAX_FAILS} попыток подряд, нужен человек"
            if args.dry_run:
                print(f"сухо: {task_id} — {reason}")
            elif _kanban(args.hermes_bin, args.board, "block", task_id, "--kind", "transient",
                         "--reason", reason):
                print(f"к человеку {task_id}: {reason}")
        # Карты, вернувшиеся в работу другим путём, из состояния убираем.
        cards = live_cards(db)
        fresh = {k: v for k, v in fresh.items() if k in cards}
        save(state_path, {**state, **fresh})
        if not pause and not to_human:
            print("отступать некому: свежих лимитов нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())

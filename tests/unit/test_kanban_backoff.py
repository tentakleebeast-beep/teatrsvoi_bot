"""Отступление после лимита провайдера: пауза растёт, пятая неудача идёт человеку.

Реального спавна нет — читается только временная база и файл состояния.

Обоснование: docs/development/125-kanban-backoff.md (правила отступления и потолка
прогонов; карта t_7f1a347d).
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BACKOFF = _load("kanban_backoff")


def _board(tmp_path: Path, status: str, outcomes: list[str]) -> Path:
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("create table tasks (id text primary key, status text not null)")
    conn.execute("insert into tasks values ('t_one', ?)", (status,))
    conn.execute("create table task_runs (id integer primary key, task_id text,"
                 " outcome text, started_at integer)")
    for i, outcome in enumerate(outcomes, start=1):
        conn.execute("insert into task_runs (id, task_id, outcome, started_at) values (?,?,?,?)",
                     (i, "t_one", outcome, 1_000 + i))
    conn.commit()
    conn.close()
    return db


def test_лимит_даёт_паузу_и_растёт(tmp_path):
    db = _board(tmp_path, "ready", ["rate_limited"])
    pause, human, state = BACKOFF.plan_marks(db, {}, now=10_000)
    assert [task for task, _ in pause] == ["t_one"]
    assert human == []
    assert state["t_one"]["fails"] == 1
    assert state["t_one"]["next"] == 10_000 + 60

    pause2, _, state2 = BACKOFF.plan_marks(db, state, now=10_000)
    assert pause2 == [("t_one", 120)]
    assert state2["t_one"]["fails"] == 2


def test_пятая_неудача_идёт_человеку(tmp_path):
    db = _board(tmp_path, "ready", ["rate_limited"])
    pause, human, state = BACKOFF.plan_marks(db, {"t_one": {"fails": 4, "next": 0}}, now=10_000)
    assert pause == []
    assert human == ["t_one"]
    assert state == {}


def test_успешный_прогон_не_тормозит(tmp_path):
    db = _board(tmp_path, "ready", ["rate_limited", "completed"])
    pause, human, state = BACKOFF.plan_marks(db, {}, now=10_000)
    assert pause == [] and human == [] and state == {}


def test_истёкшая_пауза_освобождается(tmp_path):
    now = 10_000
    assert BACKOFF.due({"t_one": {"fails": 1, "next": now - 1}}, now=now) == ["t_one"]
    assert BACKOFF.due({"t_one": {"fails": 1, "next": now + 60}}, now=now) == []

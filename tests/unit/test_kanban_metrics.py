"""Метрика конвейера считает то, что заявляет (scripts/kanban_metrics.py).

Правила и требования раздела: docs/plans/89-autonomous-development.md.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _script():
    path = ROOT / "scripts" / "kanban_metrics.py"
    spec = importlib.util.spec_from_file_location("kanban_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table tasks (id text primary key, title text, body text, assignee text,
                            status text not null, created_at integer not null,
                            completed_at integer, started_at integer);
        create table task_runs (id integer primary key autoincrement, task_id text,
                                outcome text, started_at integer not null);
        create table task_events (id integer primary key autoincrement, task_id text,
                                  kind text not null, payload text, created_at integer not null);
    """)
    now = 1_800_000_000
    # две закрытые карты: 1 и 3 часа работы
    conn.execute("insert into tasks values ('t1','Карта 1','тело','w','done',?,?,?)",
                 (now - 3600, now, now - 3600))
    conn.execute("insert into tasks values ('t2','Карта 2','тело','w','done',?,?,?)",
                 (now - 3 * 3600, now, now - 3 * 3600))
    # одна в очереди
    conn.execute("insert into tasks values ('t3','Карта 3','тело','w','ready',?,null,null)",
                 (now - 600,))
    # прогоны: у t1 один, у t2 два, один из них — возврат на доработку
    conn.executemany("insert into task_runs (task_id, outcome, started_at) values (?,?,?)",
                     [("t1", "completed", now - 3600), ("t2", "changes_requested", now - 3 * 3600),
                      ("t2", "completed", now - 1200)])
    # t2 заведена другой картой — значит это «хвост»
    conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                 "values ('t2','created','{\"creator_task_id\": \"t1\"}', ?)", (now - 3 * 3600,))
    conn.commit()
    conn.close()
    return db


def test_считает_цикл_прогоны_и_хвосты(tmp_path):
    m = _script().collect(_db(tmp_path), days=7)
    assert m["done"] == 2
    assert m["cycle_median_h"] == 2.0          # медиана 1 и 3 часов
    assert m["runs_total"] == 3
    assert m["runs_per_card"] == 1.5           # 3 прогона на 2 карты
    assert m["rework"] == 1
    # t2 заведена ДО закрытия t1 — это разбиение плана, а не хвост.
    assert m["children_cards"] == 1
    assert m["decomposed"] == 1
    assert m["tails_after_close"] == 0
    assert m["open"] == 1
    assert m["blocked"] == 0


def test_ребёнок_после_закрытия_считается_хвостом(tmp_path):
    """Родитель закрыт, работа всплыла отдельной картой — это уже недоделка."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    # t3 появилась на 10 минут позже, чем закрылась t1 — значит это хвост
    conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                 "values ('t3','created','{\"creator_task_id\": \"t1\"}', ?)",
                 (1_800_000_000 + 600,))
    conn.commit()
    conn.close()
    m = _script().collect(db, days=7)
    assert m["children_cards"] == 2
    assert m["decomposed"] == 1
    assert m["tails_after_close"] == 1
    assert [t["id"] for t in m["tails"]] == ["t3"]


def test_пустая_доска_не_падает(tmp_path):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table tasks (id text primary key, title text, body text, assignee text,
                            status text not null, created_at integer not null,
                            completed_at integer, started_at integer);
        create table task_runs (id integer primary key autoincrement, task_id text,
                                outcome text, started_at integer not null);
        create table task_events (id integer primary key autoincrement, task_id text,
                                  kind text not null, payload text, created_at integer not null);
    """)
    conn.close()
    m = _script().collect(db, days=7)
    assert m["done"] == 0 and m["cycle_median_h"] is None and m["open"] == 0

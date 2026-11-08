"""Порядок очереди канбана: приоритет → возраст, а не «на глаз».

Правило классов — docs/development/26 §9. Скрипт `scripts/kanban_queue.py`
печатает очередь в том порядке, в котором её берёт диспетчер
(`priority DESC, created_at ASC`, hermes_cli/kanban_db_dispatch.py).
Тест подставляет временную базу с известными картами и фиксирует порядок,
объяснение первой карты и отчёт о картах без класса.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HOUR = 3600
NOW = 1_800_000_000


def _script():
    path = ROOT / "scripts" / "kanban_queue.py"
    spec = importlib.util.spec_from_file_location("kanban_queue", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _board(tmp_path: Path, rows: list[tuple]) -> Path:
    """rows: (id, status, priority, age_hours, assignee, title)."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table tasks (id text primary key, title text not null, status text not null, "
        "priority integer, created_at integer not null, assignee text)")
    for tid, status, priority, age_h, assignee, title in rows:
        conn.execute("insert into tasks values (?,?,?,?,?,?)",
                     (tid, title, status, priority, NOW - int(age_h * HOUR), assignee))
    conn.commit()
    conn.close()
    return db


def test_порядок_приоритет_затем_возраст(tmp_path):
    """P0 берётся раньше P1, P1 раньше P2 — возраст не перебивает класс."""
    db = _board(tmp_path, [
        ("t_p2", "ready", 3, 100, "worker-feature", "P2 старая"),
        ("t_p0", "ready", 5, 1, "worker-feature", "P0 свежая"),
        ("t_p1", "ready", 4, 50, "worker-feature", "P1 средняя"),
    ])
    module = _script()
    report = module.build_report(module.read_cards(db), NOW)
    assert [c["id"] for c in report["queue"]] == ["t_p0", "t_p1", "t_p2"]
    assert [c["class"] for c in report["queue"]] == ["P0", "P1", "P2"]


def test_при_равном_приоритете_старшая_первая(tmp_path):
    db = _board(tmp_path, [
        ("t_new", "ready", 4, 2, "worker-feature", "P1 новая"),
        ("t_old", "ready", 4, 30, "worker-feature", "P1 старая"),
        ("t_oldest", "ready", 4, 90, "worker-feature", "P1 самая старая"),
    ])
    module = _script()
    report = module.build_report(module.read_cards(db), NOW)
    assert [c["id"] for c in report["queue"]] == ["t_oldest", "t_old", "t_new"]


def test_следующая_карта_и_объяснение(tmp_path):
    """Следующая — первая готовая; blocked её не обгоняет и не путает объяснение."""
    db = _board(tmp_path, [
        ("t_blocked", "blocked", 5, 1, "worker-feature", "P0 ждёт человека"),
        ("t_next", "ready", 4, 10, "worker-feature", "P1 в работе"),
        ("t_later", "ready", 4, 5, "worker-feature", "P1 помоложе"),
        ("t_todo", "todo", 5, 100, "", "P0 ждёт родителя"),
    ])
    module = _script()
    report = module.build_report(module.read_cards(db), NOW)
    assert report["next"]["id"] == "t_next"
    why = report["next"]["why"]
    assert "P1 (4)" in why
    assert "старше" in why            # при равном приоритете решает возраст
    assert "10.0 ч" in why            # объяснение числами, а не словами «важнее»


def test_карта_без_класса_попадает_в_отчёт(tmp_path):
    db = _board(tmp_path, [
        ("t_zero", "ready", 0, 10, "worker-feature", "Приоритет ноль"),
        ("t_null", "todo", None, 20, "", "Приоритет пустой"),
        ("t_five", "ready", 5, 30, "worker-feature", "P0"),
    ])
    module = _script()
    report = module.build_report(module.read_cards(db), NOW)
    assert {c["id"] for c in report["unclassified"]} == {"t_zero", "t_null"}
    assert report["ok"] is False
    assert [c["id"] for c in report["queue"]][0] == "t_five"


def test_архив_и_выполненные_не_попадают_в_очередь(tmp_path):
    db = _board(tmp_path, [
        ("t_done", "done", 5, 1, "worker-feature", "Закрытая"),
        ("t_arch", "archived", 0, 2, "worker-feature", "Архив"),
        ("t_ready", "ready", 3, 3, "worker-feature", "Живая"),
    ])
    module = _script()
    report = module.build_report(module.read_cards(db), NOW)
    ids = {c["id"] for c in report["queue"] + report["outside"]}
    assert ids == {"t_ready"}


def test_json_и_strict_одной_командой(tmp_path, monkeypatch, capsys):
    """CLI: --json читается машиной, --strict даёт код 1 на карте без класса."""
    module = _script()
    db_dir = tmp_path / "kanban" / "boards" / "teatrsvoi"
    db_dir.mkdir(parents=True)
    _board(db_dir, [
        ("t_p2", "ready", 3, 5, "worker-feature", "P2"),
        ("t_zero", "blocked", 0, 10, "worker-feature", "Без класса"),
    ])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["kanban_queue.py", "--board", "teatrsvoi", "--json"])
    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"] == "teatrsvoi"
    assert payload["next"]["id"] == "t_p2"
    assert [c["id"] for c in payload["unclassified"]] == ["t_zero"]

    monkeypatch.setattr(sys, "argv",
                        ["kanban_queue.py", "--board", "teatrsvoi", "--json", "--strict"])
    assert module.main() == 1

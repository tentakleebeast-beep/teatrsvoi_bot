"""Проверка правила «карта без описания не заводится» (docs/development/26 §7a, п.7).

Скрипт `scripts/check_kanban_cards.py` читает базы канбана Hermes. Тест подставляет
временную базу с известным набором карт и проверяет, что пустые находятся, а архив
и выполненные — нет.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _script():
    path = ROOT / "scripts" / "check_kanban_cards.py"
    spec = importlib.util.spec_from_file_location("check_kanban_cards", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _board(tmp_path: Path, rows: list[tuple[str, str, str, str | None]]) -> Path:
    """rows: (id, status, title, body)."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table tasks (id text primary key, title text not null, body text, "
        "assignee text, status text not null, created_at integer not null)")
    for i, (tid, status, title, body) in enumerate(rows):
        conn.execute("insert into tasks (id, title, body, assignee, status, created_at) "
                     "values (?,?,?,?,?,?)", (tid, title, body, None, status, i))
    conn.commit()
    conn.close()
    return db


def test_находит_карты_без_описания(tmp_path):
    db = _board(tmp_path, [
        ("t_empty", "ready", "Пустая карта", None),
        ("t_blank", "running", "Пробелы вместо описания", "   "),
        ("t_full", "ready", "С описанием", "Что и зачем меняем"),
    ])
    found = {card["id"] for card in _script().empty_cards(db)}
    assert found == {"t_empty", "t_blank"}, "пустые карты должны находиться"


def test_карта_без_исполнителя_не_поедет(tmp_path):
    """ready без assignee диспетчер пропускает — карта висит вечно."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("create table tasks (id text primary key, title text not null, body text, "
                 "assignee text, status text not null, created_at integer not null)")
    conn.execute("create table task_comments (id integer primary key, task_id text, text text)")
    conn.execute("insert into tasks values ('t1','Без исполнителя','тело',null,'ready',1)")
    conn.execute("insert into tasks values ('t2','С исполнителем','тело','worker-feature','ready',2)")
    conn.execute("insert into tasks values ('t3','Блокировка без причины','тело',null,'blocked',3)")
    conn.execute("insert into tasks values ('t4','Блокировка с причиной','тело',null,'blocked',4)")
    conn.execute("insert into task_comments (task_id, text) values ('t4','нужен токен')")
    conn.commit()
    conn.close()

    stuck = {c["id"]: c["issue"] for c in _script().problems(db)}
    assert "t1" in stuck and "исполнител" in stuck["t1"]
    assert "t3" in stuck and "причин" in stuck["t3"]
    assert "t2" not in stuck          # есть исполнитель
    assert "t4" not in stuck          # у блокировки есть комментарий с причиной


def test_архив_и_выполненные_не_проверяются(tmp_path):
    db = _board(tmp_path, [
        ("t_arch", "archived", "Старая карта", None),
        ("t_done", "done", "Закрытая карта", None),
        ("t_ready", "ready", "Живая карта", None),
    ])
    found = {card["id"] for card in _script().empty_cards(db)}
    assert found == {"t_ready"}, "историю не переписываем — проверяем только карты в работе"

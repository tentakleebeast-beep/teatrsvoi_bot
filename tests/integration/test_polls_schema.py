"""Миграции 065 и 066: восемь таблиц опросов, права admin и org, повторный прогон не меняет (AC-1).

Проверяется то, что легко потерять при правке SQL: идемпотентность файлов,
наличие `poll.manage`/`poll.results.all` у admin, `poll.manage` (и только он) у org
(V-088, 162 §3 D8) и то, что alembic доехал до `067_poll_interview_draft` — то есть
схема одна и там, и там.

Обоснование: FR-G18, AC-1, `docs/development/162-bot-polls.md` §14.1.
"""

from __future__ import annotations

import pathlib

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[2]
SQL = ROOT / "db" / "init" / "065_polls.sql"
SQL_066 = ROOT / "db" / "init" / "066_polls_org_manage.sql"

TABLES = ("poll", "poll_question", "poll_option", "poll_audience", "poll_person",
          "poll_run", "poll_answer", "poll_viewer")


def test_alembic_дошёл_до_068(test_db: str) -> None:
    with psycopg.connect(test_db, row_factory=dict_row) as conn:
        head = conn.execute("select version_num from alembic_version").fetchone()
    assert head["version_num"] == "068_org_without_approve"


def test_восемь_таблиц_на_месте(test_db: str) -> None:
    with psycopg.connect(test_db, row_factory=dict_row) as conn:
        rows = conn.execute(
            "select tablename from pg_tables where schemaname='public' "
            "and tablename = any(%s)", (list(TABLES),)).fetchall()
    assert sorted(row["tablename"] for row in rows) == sorted(TABLES)


def test_повторный_прогон_файла_ничего_не_меняет(test_db: str) -> None:
    """AC-1: второй прогон 065 на живой схеме не падает и не добавляет строк."""
    sql = SQL.read_text(encoding="utf-8")
    with psycopg.connect(test_db, autocommit=True) as conn:
        for _ in range(2):
            conn.execute(sql)
        caps = conn.execute(
            "select code from rbac_capability where grp = 'polls' order by code").fetchall()
        admin = conn.execute(
            "select capability from rbac_role_capability "
            "where role = 'admin' and capability like 'poll%%' order by capability").fetchall()
    assert [row[0] for row in caps] == ["poll.manage", "poll.results.all"]
    assert [row[0] for row in admin] == ["poll.manage", "poll.results.all"]


def test_org_получает_poll_manage_но_не_все_результаты(test_db: str) -> None:
    """V-088 (б): организатор ведёт опросы сам, но чужие результаты ему не открыты (162 §3 D8)."""
    with psycopg.connect(test_db) as conn:
        rows = conn.execute(
            "select capability from rbac_role_capability "
            "where role = 'org' and capability like 'poll%%' order by capability"
        ).fetchall()
    assert [row[0] for row in rows] == ["poll.manage"]


def test_повторный_прогон_066_ничего_не_добавляет(test_db: str) -> None:
    """Идемпотентность 066: второй прогон не дублирует строку матрицы прав."""
    sql = SQL_066.read_text(encoding="utf-8")
    with psycopg.connect(test_db, autocommit=True) as conn:
        for _ in range(2):
            conn.execute(sql)
        rows = conn.execute(
            "select capability from rbac_role_capability "
            "where role = 'org' and capability like 'poll%%'").fetchall()
    assert [row[0] for row in rows] == ["poll.manage"]


def test_свободный_ответ_запрещён_схемой_у_типов_без_списка(test_db: str) -> None:
    """Держит не только код: `check (kind in ('single','multi') or free_text = false)`."""
    with psycopg.connect(test_db, autocommit=True) as conn:
        author = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (785001, 'Автор Схемы', 'автор-схемы', 'admin', 'active') "
            "returning id").fetchone()[0]
        poll_id = conn.execute(
            "insert into poll (title, created_by) values ('Т', %s) returning id",
            (author,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into poll_question (poll_id, position, text, kind, free_text) "
                "values (%s, 1, 'Да?', 'yesno', true)", (poll_id,))

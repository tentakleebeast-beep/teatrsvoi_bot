"""Накат схемы Alembic на тестовую БД: версия = head, повтор — no-op.

Проверяется то, на чём держится единый источник схемы (docs/reports/132):
  * в `alembic_version` ровно одна строка и это голова цепочки;
  * повторный `upgrade head` ничего не меняет (идемпотентность деплоя);
  * поздние файлы действительно доехали — проверяем маркеры в схеме, а не факт
    «файл есть в списке»;
  * `deploy/check_schema.py --expect-head` (read-only) возвращает 0 — им baseline
    после восстановления из дампа.

Обоснование: docs/reports/132-db-schema-single-source-plan.md §5; дефект 30.10.2026 —
на проде не хватало `participant.write_gender` и `budget_tx.is_plan`.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import psycopg
import pytest

from tests.conftest import _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def schema_version(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("select version_num from alembic_version")
            return [row[0] for row in cur.fetchall()]


def columns(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select table_name || '.' || column_name from information_schema.columns "
                "where table_schema = 'public'")
            return {row[0] for row in cur.fetchall()}


def test_version_is_single_head(test_db) -> None:
    from app.schema_version import head_revision  # noqa: PLC0415

    assert schema_version(test_db) == [head_revision()]


def test_repeated_upgrade_is_noop(test_db) -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    before = schema_version(test_db)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = test_db
    command.upgrade(cfg, "head")
    assert schema_version(test_db) == before


def test_late_files_reached_the_schema(test_db) -> None:
    present = columns(test_db)
    for marker in ("participant.is_active", "participant_role.revoked_at",
                   "intent_log.steps"):
        assert marker in present, marker


def test_check_schema_expect_head_returns_zero(test_db) -> None:
    env = dict(os.environ, DATABASE_URL=test_db)
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "check_schema.py"), "--expect-head"],
        capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_schema_expect_head_is_cwd_independent(test_db, tmp_path) -> None:
    """Флаг работает не из корня репозитория: деплой вызывает его без cd.

    `script_location` в alembic.ini задан через `%(here)s`; с относительным путём
    вызов из другого каталога падал «Path doesn't exist: services/tool-api/migrations».
    """
    env = dict(os.environ, DATABASE_URL=test_db)
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "check_schema.py"), "--expect-head"],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "062_files_studio_access" in result.stdout


# --- «SQL накатан, версия не сдвинута» (дефект 08.11.2026, выкат v0.8.0) --------
#
# repair-schema.yml льёт файлы в psql, тот про Alembic не знает: структура на проде
# уехала вперёд, alembic_version остался на 060, и выкат остановился на сверке версии
# и откатился. Здесь проверяется, что учёт догоняет структуру и что догнать её он
# может только тогда, когда структура действительно на месте.
# Обоснование: docs/infrastructure/35 §10.2, docs/reports/132.


def run_check_schema(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, DATABASE_URL=dsn)
    return subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "check_schema.py"), *args],
        capture_output=True, text=True, env=env, check=False)


def set_version(dsn: str, version: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("update alembic_version set version_num = %s", (version,))


def revisions() -> list[str]:
    """Цепочка от head к началу — нужны две-три последние ревизии."""
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    head = script.get_heads()[0]
    return [rev.revision for rev in script.iterate_revisions(head, "base")]


def test_stamp_head_puts_version_to_head_after_sql_apply(test_db) -> None:
    """Версия догоняет структуру: файл применён psql-ом — учёт ставится на его ревизию."""
    head = revisions()[0]
    prev = revisions()[1]
    set_version(test_db, prev)
    try:
        result = run_check_schema(test_db, "--stamp-head", "--files", f"db/init/{head}.sql")
        assert result.returncode == 0, result.stdout + result.stderr
        assert schema_version(test_db) == [head]
        assert f"alembic current: {head}" in result.stdout
        assert f"head: {head}" in result.stdout
    finally:
        set_version(test_db, head)


def test_stamp_head_does_not_skip_unapplied_revisions(test_db) -> None:
    """Версия встаёт по названным файлам, а не на head: учёт не обгоняет базу."""
    head, middle, old = revisions()[:3]
    set_version(test_db, old)
    try:
        result = run_check_schema(
            test_db, "--stamp-head", "--files", f"db/init/{middle}.sql")
        assert result.returncode == 1, result.stdout + result.stderr
        assert schema_version(test_db) == [middle]
        assert head in result.stdout
        assert f"db/init/{head}.sql" in result.stdout
    finally:
        set_version(test_db, head)


def test_stamp_head_refuses_when_structure_is_behind(test_db) -> None:
    """Структура отстаёт — версию не двигаем: иначе учёт обещает выкату пустоту."""
    base = test_db.rsplit("/", 1)[0]
    name = f"svoi_test_version_{os.getpid()}"
    prev = revisions()[1]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{name}"')
        conn.execute(f'create database "{name}"')
    empty = f"{base}/{name}"
    try:
        with psycopg.connect(empty, autocommit=True) as conn:
            conn.execute("create table alembic_version (version_num varchar(64) primary key)")
            conn.execute("insert into alembic_version values (%s)", (prev,))
        result = run_check_schema(empty, "--stamp-head", "--files", "db/init/061_master_role.sql")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "структура отстаёт от db/init" in result.stdout
        assert schema_version(empty) == [prev]
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'drop database if exists "{name}"')


def test_expect_head_reports_skew_without_changing_it(test_db) -> None:
    """Read-only проверка: версия отстала — код 1 и понятное сообщение, база не тронута."""
    head, prev = revisions()[:2]
    set_version(test_db, prev)
    try:
        result = run_check_schema(test_db, "--expect-head")
        assert result.returncode == 1, result.stdout + result.stderr
        assert f"версия схемы {prev} не равна head {head}" in result.stdout
        assert schema_version(test_db) == [prev]
    finally:
        set_version(test_db, head)

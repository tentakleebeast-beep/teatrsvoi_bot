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
    assert "060_participant_role_revoked" in result.stdout

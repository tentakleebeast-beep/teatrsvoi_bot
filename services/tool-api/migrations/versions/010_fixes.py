"""db/init/010_fixes.sql

Revision ID: 010_fixes
Revises: 009_budget_confirm
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "010_fixes"
down_revision = "009_budget_confirm"
branch_labels = None
depends_on = None

SQL_FILE = "010_fixes.sql"
SQL_SHA256 = "7feeb0cc22217495742c28ed4f364643818799b38774105781b85ef32c6ec413"


def upgrade() -> None:
    """Накат db/init/010_fixes.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

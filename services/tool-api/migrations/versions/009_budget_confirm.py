"""db/init/009_budget_confirm.sql

Revision ID: 009_budget_confirm
Revises: 008_rbac_capabilities
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "009_budget_confirm"
down_revision = "008_rbac_capabilities"
branch_labels = None
depends_on = None

SQL_FILE = "009_budget_confirm.sql"
SQL_SHA256 = "0740ce8f55325bc10312f55aec2da5dfd4a3bf7f44c23a6ceeefbe72f686b438"


def upgrade() -> None:
    """Накат db/init/009_budget_confirm.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

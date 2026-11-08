"""db/init/032_finance_granularity.sql

Revision ID: 032_finance_granularity
Revises: 031_feedback_and_manifest
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "032_finance_granularity"
down_revision = "031_feedback_and_manifest"
branch_labels = None
depends_on = None

SQL_FILE = "032_finance_granularity.sql"
SQL_SHA256 = "b10f3bce20575750bee5d23fac76e279dcc1e12dd567bd2602e6ad64091f4a55"


def upgrade() -> None:
    """Накат db/init/032_finance_granularity.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

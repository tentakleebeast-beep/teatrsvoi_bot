"""db/init/033_sheet_write_state.sql

Revision ID: 033_sheet_write_state
Revises: 032_finance_granularity
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "033_sheet_write_state"
down_revision = "032_finance_granularity"
branch_labels = None
depends_on = None

SQL_FILE = "033_sheet_write_state.sql"
SQL_SHA256 = "e06adba0de8bb8cdddb12a28fb823b11aa23bda43fb5092674af212a84d05438"


def upgrade() -> None:
    """Накат db/init/033_sheet_write_state.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

"""db/init/034_dues_tariff.sql

Revision ID: 034_dues_tariff
Revises: 033_sheet_write_state
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "034_dues_tariff"
down_revision = "033_sheet_write_state"
branch_labels = None
depends_on = None

SQL_FILE = "034_dues_tariff.sql"
SQL_SHA256 = "824c00ecbf4ca45f0d8752120875277b0e3a5737bfc33b89cb73699ab8611de5"


def upgrade() -> None:
    """Накат db/init/034_dues_tariff.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

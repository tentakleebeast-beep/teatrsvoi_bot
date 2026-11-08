"""db/init/020_approvals.sql

Revision ID: 020_approvals
Revises: 019_migration_marker
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "020_approvals"
down_revision = "019_migration_marker"
branch_labels = None
depends_on = None

SQL_FILE = "020_approvals.sql"
SQL_SHA256 = "4c582114b0edbcc1fe4b198f52c3331d5f5882791d81dcf2b23b670a3d05b01d"


def upgrade() -> None:
    """Накат db/init/020_approvals.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

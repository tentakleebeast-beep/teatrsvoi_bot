"""db/init/061_master_role.sql

Revision ID: 061_master_role
Revises: 060_participant_role_revoked
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "061_master_role"
down_revision = "060_participant_role_revoked"
branch_labels = None
depends_on = None

SQL_FILE = "061_master_role.sql"
SQL_SHA256 = "8fd5c90cf2bd2db4b6bd67de5315ab1285982117bb4dd90a4e48baa32cb81a23"


def upgrade() -> None:
    """Накат db/init/061_master_role.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

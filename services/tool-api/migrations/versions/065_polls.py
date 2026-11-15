"""db/init/065_polls.sql

Revision ID: 065_polls
Revises: 064_master_invite
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "065_polls"
down_revision = "064_master_invite"
branch_labels = None
depends_on = None

SQL_FILE = "065_polls.sql"
SQL_SHA256 = "3bc32904ced4f718b1ccea3de9ba9cfa9282ee5549fd0cab2f2fd6c0786cde7d"


def upgrade() -> None:
    """Накат db/init/065_polls.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

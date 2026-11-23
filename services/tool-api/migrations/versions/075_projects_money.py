"""db/init/075_projects_money.sql

Revision ID: 075_projects_money
Revises: 073_files_two_locations
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "075_projects_money"
down_revision = "073_files_two_locations"
branch_labels = None
depends_on = None

SQL_FILE = "075_projects_money.sql"
SQL_SHA256 = "f58ad1f368c62a27dd1852f7dc419281e2bcbc188164c621c1f745d28201a063"


def upgrade() -> None:
    """Накат db/init/075_projects_money.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

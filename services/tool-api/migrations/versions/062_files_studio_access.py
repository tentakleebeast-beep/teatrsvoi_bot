"""db/init/062_files_studio_access.sql

Revision ID: 062_files_studio_access
Revises: 061_master_role
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "062_files_studio_access"
down_revision = "061_master_role"
branch_labels = None
depends_on = None

SQL_FILE = "062_files_studio_access.sql"
SQL_SHA256 = "9c2e80558da8c5127da26e2c195864fd9125b4b37229b481ad236db89354febd"


def upgrade() -> None:
    """Накат db/init/062_files_studio_access.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

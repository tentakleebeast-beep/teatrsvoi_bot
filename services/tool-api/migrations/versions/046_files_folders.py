"""db/init/046_files_folders.sql

Revision ID: 046_files_folders
Revises: 045_tx_plan_flag
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "046_files_folders"
down_revision = "045_tx_plan_flag"
branch_labels = None
depends_on = None

SQL_FILE = "046_files_folders.sql"
SQL_SHA256 = "8c5ca2ff3657acc02914f838acd082d53578d873dbda3901d0278060e44f5994"


def upgrade() -> None:
    """Накат db/init/046_files_folders.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

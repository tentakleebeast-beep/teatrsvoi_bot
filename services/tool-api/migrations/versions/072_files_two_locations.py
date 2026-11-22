"""db/init/072_files_two_locations.sql

Revision ID: 072_files_two_locations
Revises: 071_drop_dead_capabilities
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "072_files_two_locations"
down_revision = "071_drop_dead_capabilities"
branch_labels = None
depends_on = None

SQL_FILE = "072_files_two_locations.sql"
SQL_SHA256 = "62ac34774ebb7cb578aa3781b0688611e428af7f958da1a4c1e7780a277db232"


def upgrade() -> None:
    """Накат db/init/072_files_two_locations.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

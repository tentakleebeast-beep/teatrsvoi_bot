"""db/init/001_schema.sql

Revision ID: 001_schema
Revises: —
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "001_schema"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = "001_schema.sql"
SQL_SHA256 = "0f84f9f8b313c1a16dacdb8befdfcaafcc9a079d06a9ae0e1b6105e4b13fe4f1"


def upgrade() -> None:
    """Накат db/init/001_schema.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

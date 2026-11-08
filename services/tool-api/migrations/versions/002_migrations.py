"""db/init/002_migrations.sql

Revision ID: 002_migrations
Revises: 001_schema
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "002_migrations"
down_revision = "001_schema"
branch_labels = None
depends_on = None

SQL_FILE = "002_migrations.sql"
SQL_SHA256 = "78fbf03737aa5c4f7ee01837bf86629e98a77d8f4bc7651f4032a7ab2702ecd1"


def upgrade() -> None:
    """Накат db/init/002_migrations.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

"""db/init/003_categories_v2.sql

Revision ID: 003_categories_v2
Revises: 002_migrations
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "003_categories_v2"
down_revision = "002_migrations"
branch_labels = None
depends_on = None

SQL_FILE = "003_categories_v2.sql"
SQL_SHA256 = "38e0b44ca2a1993ab1deb75dfc9921967d9d3a22651ff94f8ad79d0978e2a45a"


def upgrade() -> None:
    """Накат db/init/003_categories_v2.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

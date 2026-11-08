"""db/init/053_income_misc_category.sql

Revision ID: 053_income_misc_category
Revises: 052_agent_cache_source
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "053_income_misc_category"
down_revision = "052_agent_cache_source"
branch_labels = None
depends_on = None

SQL_FILE = "053_income_misc_category.sql"
SQL_SHA256 = "b114ab1b94afbbf2f27f3220c335e78d59412ae174488e4527bc101dd9a78c71"


def upgrade() -> None:
    """Накат db/init/053_income_misc_category.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

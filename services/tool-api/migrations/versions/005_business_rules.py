"""db/init/005_business_rules.sql

Revision ID: 005_business_rules
Revises: 004_people_fix
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "005_business_rules"
down_revision = "004_people_fix"
branch_labels = None
depends_on = None

SQL_FILE = "005_business_rules.sql"
SQL_SHA256 = "db5fd23e6baa3f114dcaf384baa256edc086a75f673266a226cce11ff49a510b"


def upgrade() -> None:
    """Накат db/init/005_business_rules.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

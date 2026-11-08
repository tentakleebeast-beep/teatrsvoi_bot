"""db/init/006_rate_base.sql

Revision ID: 006_rate_base
Revises: 005_business_rules
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "006_rate_base"
down_revision = "005_business_rules"
branch_labels = None
depends_on = None

SQL_FILE = "006_rate_base.sql"
SQL_SHA256 = "fa1237d52b54572f453564b86f8cc5b2222c576a5c7214ac02d2e241b4f6b5c9"


def upgrade() -> None:
    """Накат db/init/006_rate_base.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

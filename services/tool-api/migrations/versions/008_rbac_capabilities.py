"""db/init/008_rbac_capabilities.sql

Revision ID: 008_rbac_capabilities
Revises: 007_llm_quota
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "008_rbac_capabilities"
down_revision = "007_llm_quota"
branch_labels = None
depends_on = None

SQL_FILE = "008_rbac_capabilities.sql"
SQL_SHA256 = "19450d98b580421a523726f84baed9f293bee5fb6cbee8f73880955aeb0a9f68"


def upgrade() -> None:
    """Накат db/init/008_rbac_capabilities.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

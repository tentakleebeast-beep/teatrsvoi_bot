"""db/init/007_llm_quota.sql

Revision ID: 007_llm_quota
Revises: 006_rate_base
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "007_llm_quota"
down_revision = "006_rate_base"
branch_labels = None
depends_on = None

SQL_FILE = "007_llm_quota.sql"
SQL_SHA256 = "d36fc827aca3de5b1bda5ae3f97fab9ab9004996c9c8ce43558fe6ae98c416ff"


def upgrade() -> None:
    """Накат db/init/007_llm_quota.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

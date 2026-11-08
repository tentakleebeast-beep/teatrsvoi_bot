"""db/init/052_agent_cache_source.sql

Revision ID: 052_agent_cache_source
Revises: 051_feedback_answer_server
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "052_agent_cache_source"
down_revision = "051_feedback_answer_server"
branch_labels = None
depends_on = None

SQL_FILE = "052_agent_cache_source.sql"
SQL_SHA256 = "fb2ae4566e63cbcdda712581537d345bb2ff09b2f6ec8d45584b8fcd30c9aa57"


def upgrade() -> None:
    """Накат db/init/052_agent_cache_source.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

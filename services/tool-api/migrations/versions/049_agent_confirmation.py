"""db/init/049_agent_confirmation.sql

Revision ID: 049_agent_confirmation
Revises: 048_agent_observability
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "049_agent_confirmation"
down_revision = "048_agent_observability"
branch_labels = None
depends_on = None

SQL_FILE = "049_agent_confirmation.sql"
SQL_SHA256 = "70547c10e80c6cc641fe2c748e764a5f27bb19aff3e001b868b81ddf8d0cec4c"


def upgrade() -> None:
    """Накат db/init/049_agent_confirmation.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

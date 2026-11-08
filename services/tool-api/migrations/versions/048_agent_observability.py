"""db/init/048_agent_observability.sql

Revision ID: 048_agent_observability
Revises: 047_feedback_answers
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "048_agent_observability"
down_revision = "047_feedback_answers"
branch_labels = None
depends_on = None

SQL_FILE = "048_agent_observability.sql"
SQL_SHA256 = "03178bfd77bb63521cf6760faa57d8c935d1e59afae4445b715e1df994afe8a4"


def upgrade() -> None:
    """Накат db/init/048_agent_observability.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

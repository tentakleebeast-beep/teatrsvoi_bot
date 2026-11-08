"""db/init/051_feedback_answer_server.sql

Revision ID: 051_feedback_answer_server
Revises: 049_agent_confirmation
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "051_feedback_answer_server"
down_revision = "049_agent_confirmation"
branch_labels = None
depends_on = None

SQL_FILE = "051_feedback_answer_server.sql"
SQL_SHA256 = "baa9e3c8bc3ebed0340add528a36a0ae6c65d634a7d0ecbd4037ad927e79822a"


def upgrade() -> None:
    """Накат db/init/051_feedback_answer_server.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

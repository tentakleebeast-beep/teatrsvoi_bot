"""db/init/047_feedback_answers.sql

Revision ID: 047_feedback_answers
Revises: 046_files_folders
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "047_feedback_answers"
down_revision = "046_files_folders"
branch_labels = None
depends_on = None

SQL_FILE = "047_feedback_answers.sql"
SQL_SHA256 = "65a4e16b34b81bce7f8f72d3bbfc5f7712c93405cc45dc1ab5f08a86ad9669bd"


def upgrade() -> None:
    """Накат db/init/047_feedback_answers.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

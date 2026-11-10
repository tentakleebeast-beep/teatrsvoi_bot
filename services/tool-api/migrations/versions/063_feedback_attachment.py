"""db/init/063_feedback_attachment.sql

Revision ID: 063_feedback_attachment
Revises: 062_files_studio_access
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "063_feedback_attachment"
down_revision = "062_files_studio_access"
branch_labels = None
depends_on = None

SQL_FILE = "063_feedback_attachment.sql"
SQL_SHA256 = "29a0b76a69e222921daf181f019552c416aae7deae133169f09aaaf01c59ec48"


def upgrade() -> None:
    """Накат db/init/063_feedback_attachment.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

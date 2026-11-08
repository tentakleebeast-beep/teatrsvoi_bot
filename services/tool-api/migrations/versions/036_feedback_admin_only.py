"""db/init/036_feedback_admin_only.sql

Revision ID: 036_feedback_admin_only
Revises: 035_participant_role_backfill
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "036_feedback_admin_only"
down_revision = "035_participant_role_backfill"
branch_labels = None
depends_on = None

SQL_FILE = "036_feedback_admin_only.sql"
SQL_SHA256 = "b3ce6a58be73c0048e5751f84e367c43eb00ac85ca1d1c33a5e03b5ef183d181"


def upgrade() -> None:
    """Накат db/init/036_feedback_admin_only.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

"""db/init/037_participant_contacts.sql

Revision ID: 037_participant_contacts
Revises: 036_feedback_admin_only
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "037_participant_contacts"
down_revision = "036_feedback_admin_only"
branch_labels = None
depends_on = None

SQL_FILE = "037_participant_contacts.sql"
SQL_SHA256 = "634b00982c6a05b02c6f13f14e4816c14b8e3037407e77a9b8e74ccd9fd863dc"


def upgrade() -> None:
    """Накат db/init/037_participant_contacts.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

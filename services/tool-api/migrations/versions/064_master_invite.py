"""db/init/064_master_invite.sql

Revision ID: 064_master_invite
Revises: 063_feedback_attachment
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "064_master_invite"
down_revision = "063_feedback_attachment"
branch_labels = None
depends_on = None

SQL_FILE = "064_master_invite.sql"
SQL_SHA256 = "549241db280dfd775eed0848a0ef837418d78c77e592e41a449ea203542c2e41"


def upgrade() -> None:
    """Накат db/init/064_master_invite.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

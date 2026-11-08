"""db/init/060_participant_role_revoked.sql

Revision ID: 060_participant_role_revoked
Revises: 058_feedback_wish_fl
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "060_participant_role_revoked"
down_revision = "058_feedback_wish_fl"
branch_labels = None
depends_on = None

SQL_FILE = "060_participant_role_revoked.sql"
SQL_SHA256 = "55829505a6f36e787cc7e2528c6dc383364a28a92a4baac8a9c879d84be9c780"


def upgrade() -> None:
    """Накат db/init/060_participant_role_revoked.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

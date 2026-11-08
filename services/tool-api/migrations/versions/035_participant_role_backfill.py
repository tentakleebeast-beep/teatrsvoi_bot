"""db/init/035_participant_role_backfill.sql

Revision ID: 035_participant_role_backfill
Revises: 034_dues_tariff
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "035_participant_role_backfill"
down_revision = "034_dues_tariff"
branch_labels = None
depends_on = None

SQL_FILE = "035_participant_role_backfill.sql"
SQL_SHA256 = "b2fbd7cb6500af3ed505d970d9eb0648bd2803c68fff06640008744df292d716"


def upgrade() -> None:
    """Накат db/init/035_participant_role_backfill.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

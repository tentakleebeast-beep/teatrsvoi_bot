"""db/init/077_event_participant.sql

Revision ID: 077_event_participant
Revises: 076_troupe_schedule_read
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "077_event_participant"
down_revision = "076_troupe_schedule_read"
branch_labels = None
depends_on = None

SQL_FILE = "077_event_participant.sql"
SQL_SHA256 = "92502d7eeb1094e300b056270343ce2c9d64e9eaa9b9ed44cd535b5b0de1a08a"


def upgrade() -> None:
    """Накат db/init/077_event_participant.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")

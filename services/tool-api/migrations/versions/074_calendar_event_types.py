"""db/init/074_calendar_event_types.sql

Revision ID: 074_calendar_event_types
Revises: 072_google_event_calendar
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "074_calendar_event_types"
down_revision = "072_google_event_calendar"
branch_labels = None
depends_on = None

SQL_FILE = "074_calendar_event_types.sql"
SQL_SHA256 = "413350f247d8170356d28ecbc23eb9878b12873d08eb5b56284398758aeb2782"


def upgrade() -> None:
    """Накат db/init/074_calendar_event_types.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
